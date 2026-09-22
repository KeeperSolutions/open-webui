"""Who may change membership of a team's PII policy group.

The guard has two checks: the group must be the caller's team policy, and every
target must be a member of that team. Each test class isolates one check, so
removing either check fails its own tests even if the other still refuses.
"""

import sys
import time
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from open_webui.models.billing import Team, TeamMember, TeamMembers
from open_webui.utils.team_scope import (
    _may_change_policy_membership,
    may_manage_team_policy,
)


OWNER, MEMBER, STRANGER = "u-owner", "u-member", "u-stranger"
OTHER_OWNER, OTHER_MEMBER = "u-owner2", "u-member2"

TEAM, OTHER_TEAM = "t1", "t2"
TEAM_GROUP, OTHER_TEAM_GROUP = "g-team-pii", "g-other-team-pii"
ADMIN_GROUP = "pii-masking-policy"


class QueryCounter:
    """Counts SELECT statements at the cursor.

    The guard runs before every membership change, so its query count is pinned.
    """

    def __init__(self):
        self.selects = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip()[:6].upper() == "SELECT":
            self.selects += 1


def _user(role="user", uid=OWNER):
    return MagicMock(id=uid, role=role)


@pytest_asyncio.fixture
async def env():
    """Two teams with their own policy groups, plus somebody in neither."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Team.__table__.create, checkfirst=True)
        await conn.run_sync(TeamMember.__table__.create, checkfirst=True)

    counter = QueryCounter()
    event.listen(engine.sync_engine, "before_cursor_execute", counter)

    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())

    def team(team_id, owner, group_id):
        return Team(
            id=team_id,
            name=team_id,
            owner_user_id=owner,
            seat_limit=10,
            monthly_credits=0,
            group_id=group_id,
            created_at=now,
            updated_at=now,
        )

    def member(team_id, user_id):
        # Owners also get `role="member"` on purpose. `teams.owner_user_id` and
        # `team_members.role` are not kept in sync, so the guard must read
        # ownership from `teams.owner_user_id`; a guard that used the role would
        # fail these tests.
        return TeamMember(
            id=f"tm-{team_id}-{user_id}",
            team_id=team_id,
            user_id=user_id,
            role="member",
            created_at=now,
        )

    session.add_all(
        [
            team(TEAM, OWNER, TEAM_GROUP),
            team(OTHER_TEAM, OTHER_OWNER, OTHER_TEAM_GROUP),
            member(TEAM, OWNER),
            member(TEAM, MEMBER),
            member(OTHER_TEAM, OTHER_OWNER),
            member(OTHER_TEAM, OTHER_MEMBER),
        ]
    )
    await session.commit()

    @asynccontextmanager
    async def _ctx(db=None):
        yield session

    # Both names must be patched. `team_ownership_of_group` imports the context
    # manager function-locally from `internal.db`, while `models.billing` holds
    # its own module-scope reference. Without the second patch, check 2 queries
    # the real database configured by DATABASE_URL.
    with patch("open_webui.internal.db.get_async_db_context", _ctx), patch(
        "open_webui.models.billing.get_async_db_context", _ctx
    ):
        counter.selects = 0
        yield counter

    event.remove(engine.sync_engine, "before_cursor_execute", counter)
    await session.close()
    await engine.dispose()


# ---------------------------------------------------------------------------
# What the guard lets through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_owner_may_act_on_their_own_member(env):
    assert await _may_change_policy_membership(_user(), TEAM_GROUP, [MEMBER]) is True


@pytest.mark.asyncio
async def test_an_owner_may_act_on_themselves(env):
    """An owner is a member of their own team, so check 2 admits them."""
    assert await _may_change_policy_membership(_user(), TEAM_GROUP, [OWNER]) is True


@pytest.mark.asyncio
async def test_an_owner_may_act_on_several_of_their_own(env):
    assert await _may_change_policy_membership(_user(), TEAM_GROUP, [OWNER, MEMBER]) is True


# ---------------------------------------------------------------------------
# Check 1: the group must be the caller's own team policy
# ---------------------------------------------------------------------------


class TestTheGroupMustBeTheirs:
    """Every case is arranged so check 2 would pass, so only check 1 can refuse.

    The target is therefore always a member of the team that owns the group.
    """

    @pytest.mark.asyncio
    async def test_another_teams_policy_is_refused(self, env):
        # The target belongs to the other team, otherwise check 2 would refuse.
        assert await _may_change_policy_membership(_user(), OTHER_TEAM_GROUP, [OTHER_MEMBER]) is False

    @pytest.mark.asyncio
    async def test_an_administrators_group_is_refused(self, env):
        """No team points at it, so no owner reaches it."""
        assert await _may_change_policy_membership(_user(), ADMIN_GROUP, [MEMBER]) is False

    @pytest.mark.asyncio
    async def test_a_group_that_does_not_exist_is_refused(self, env):
        assert await _may_change_policy_membership(_user(), "g-nothing", [MEMBER]) is False

    @pytest.mark.asyncio
    async def test_an_empty_group_id_is_refused(self, env):
        assert await _may_change_policy_membership(_user(), "", [MEMBER]) is False

    @pytest.mark.asyncio
    async def test_a_plain_member_of_the_team_is_refused(self, env):
        """Ownership, not membership. The team is right; the caller is not."""
        assert await _may_change_policy_membership(_user(uid=MEMBER), TEAM_GROUP, [MEMBER]) is False

    @pytest.mark.asyncio
    async def test_the_other_teams_owner_is_refused(self, env):
        assert (
            await _may_change_policy_membership(_user(uid=OTHER_OWNER), TEAM_GROUP, [MEMBER])
            is False
        )


# ---------------------------------------------------------------------------
# Check 2: every target must be a member of that team
# ---------------------------------------------------------------------------


class TestEveryTargetMustBeTheirs:
    """Every case uses the owner's own policy group, so only check 2 can refuse."""

    @pytest.mark.asyncio
    async def test_a_stranger_is_refused(self, env):
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [STRANGER]) is False

    @pytest.mark.asyncio
    async def test_another_teams_member_is_refused(self, env):
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [OTHER_MEMBER]) is False

    @pytest.mark.asyncio
    async def test_a_mixed_request_is_refused_whole(self, env):
        """The request is refused whole, not trimmed to the allowed targets.

        Partial success would let an owner probe who exists outside their team.
        """
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [MEMBER, STRANGER]) is False

    @pytest.mark.asyncio
    async def test_a_duplicated_member_is_still_allowed(self, env):
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [MEMBER, MEMBER]) is True


# ---------------------------------------------------------------------------
# An empty request
# ---------------------------------------------------------------------------


class TestAnEmptyRequestFailsClosed:
    """An empty target list is refused for every role, before the role is read.

    Otherwise check 2 would pass vacuously and the guard would fail open. Callers
    that treat an empty body as a no-op must short-circuit before asking.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("targets", [[], None])
    async def test_an_owner_with_no_targets_is_refused(self, env, targets):
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, targets) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("targets", [[], None])
    async def test_an_admin_with_no_targets_is_refused(self, env, targets):
        assert (
            await _may_change_policy_membership(_user(role="admin"), TEAM_GROUP, targets) is False
        )

    @pytest.mark.asyncio
    async def test_an_empty_request_costs_no_query(self, env):
        counter = env
        await _may_change_policy_membership(_user(), TEAM_GROUP, [])
        assert counter.selects == 0


# ---------------------------------------------------------------------------
# The administrator
# ---------------------------------------------------------------------------


class TestTheAdministratorIsUnbounded:
    @pytest.mark.asyncio
    async def test_an_admin_may_act_on_another_teams_policy(self, env):
        assert (
            await _may_change_policy_membership(
                _user(role="admin"), OTHER_TEAM_GROUP, [OTHER_MEMBER]
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_an_admin_may_act_on_someone_in_no_team(self, env):
        assert (
            await _may_change_policy_membership(_user(role="admin"), ADMIN_GROUP, [STRANGER]) is True
        )


# ---------------------------------------------------------------------------
# Query cost
# ---------------------------------------------------------------------------


class TestTheCostIsMeasured:
    @pytest.mark.asyncio
    async def test_an_admin_costs_no_query(self, env):
        counter = env
        assert await _may_change_policy_membership(_user(role="admin"), TEAM_GROUP, [MEMBER]) is True
        assert counter.selects == 0

    @pytest.mark.asyncio
    async def test_an_owner_costs_exactly_two_queries(self, env):
        counter = env
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [MEMBER]) is True
        assert counter.selects == 2, "one for the team, one for the membership"

    @pytest.mark.asyncio
    async def test_the_cost_does_not_grow_with_the_number_of_targets(self, env):
        """Membership is checked with one query for all targets, not one per target.

        The dashboard can name a whole team in one request.
        """
        counter = env
        await _may_change_policy_membership(_user(), TEAM_GROUP, [OWNER, MEMBER])
        assert counter.selects == 2

    @pytest.mark.asyncio
    async def test_a_refused_group_stops_before_the_membership_query(self, env):
        """Check 1 refusing means check 2 is never paid for."""
        counter = env
        assert await _may_change_policy_membership(_user(), ADMIN_GROUP, [MEMBER]) is False
        assert counter.selects == 1


# ---------------------------------------------------------------------------
# Routers do not call the boolean guard directly
# ---------------------------------------------------------------------------


def test_no_route_calls_the_guard_yet():
    """Routers must call `authorise_policy_membership_change`, which raises.

    Calling the boolean guard directly means writing `if not await guard(...)`,
    where a missing `not` makes the route fail open.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    callers = set()
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name == "team_scope.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "_may_change_policy_membership":
                callers.add(str(path.relative_to(root)))

    assert callers == set(), sorted(callers)


# ---------------------------------------------------------------------------
# The model method underneath check 2
# ---------------------------------------------------------------------------


class TestMembersAmong:
    """Tested directly, because the guard never passes it an empty list.

    Other callers of this model method may not refuse empty input first.
    """

    @pytest.mark.asyncio
    async def test_it_returns_only_this_teams_members(self, env):
        assert await TeamMembers.members_among(TEAM, [OWNER, MEMBER, OTHER_MEMBER, STRANGER]) == {
            OWNER,
            MEMBER,
        }

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_simply_absent(self, env):
        assert await TeamMembers.members_among(TEAM, [STRANGER]) == set()

    @pytest.mark.asyncio
    async def test_an_empty_list_costs_no_query(self, env):
        counter = env
        assert await TeamMembers.members_among(TEAM, []) == set()
        assert counter.selects == 0

    @pytest.mark.asyncio
    async def test_one_query_however_many_ids(self, env):
        counter = env
        await TeamMembers.members_among(TEAM, [OWNER, MEMBER, OTHER_MEMBER, STRANGER])
        assert counter.selects == 1


# ---------------------------------------------------------------------------
# Check 1 on its own, as the response flag
# ---------------------------------------------------------------------------


class TestMayManageTeamPolicy:
    """The flag the directory reports, computed from check 1 only.

    Tested here because a plain member cannot reach the route:
    `_may_read_team_dashboard` refuses them. The flag must not be derived from
    "this view is team-scoped", or widening that dashboard guard would grant a
    plain member the owner's power.
    """

    @pytest.mark.asyncio
    async def test_the_owner_may(self, env):
        assert await may_manage_team_policy(_user(), TEAM_GROUP) is True

    @pytest.mark.asyncio
    async def test_an_admin_may_anywhere(self, env):
        assert await may_manage_team_policy(_user(role="admin"), OTHER_TEAM_GROUP) is True

    @pytest.mark.asyncio
    async def test_a_plain_member_may_not(self, env):
        """Not reachable through the route, so it is asserted on the function."""
        assert await may_manage_team_policy(_user(uid=MEMBER), TEAM_GROUP) is False

    @pytest.mark.asyncio
    async def test_another_teams_owner_may_not(self, env):
        assert await may_manage_team_policy(_user(uid=OTHER_OWNER), TEAM_GROUP) is False

    @pytest.mark.asyncio
    async def test_an_administrators_group_belongs_to_no_owner(self, env):
        assert await may_manage_team_policy(_user(), ADMIN_GROUP) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("group_id", [None, ""])
    async def test_no_group_means_nothing_to_govern_for_anyone(self, env, group_id):
        """Including an administrator: there is no group to be in charge of."""
        assert await may_manage_team_policy(_user(), group_id) is False
        assert await may_manage_team_policy(_user(role="admin"), group_id) is False

    @pytest.mark.asyncio
    async def test_an_admin_costs_no_query(self, env):
        counter = env
        assert await may_manage_team_policy(_user(role="admin"), TEAM_GROUP) is True
        assert counter.selects == 0

    @pytest.mark.asyncio
    async def test_an_owner_costs_exactly_one_query(self, env):
        """The flag runs check 1 only, so it costs one query, not two."""
        counter = env
        await may_manage_team_policy(_user(), TEAM_GROUP)
        assert counter.selects == 1

    @pytest.mark.asyncio
    async def test_it_agrees_with_the_guard_wherever_the_guard_has_targets(self, env):
        """Wherever the flag says no, the guard also refuses.

        Otherwise the UI would hide or show the button inconsistently with what
        the server allows.
        """
        for caller, group in [
            (_user(), TEAM_GROUP),
            (_user(uid=MEMBER), TEAM_GROUP),
            (_user(uid=OTHER_OWNER), TEAM_GROUP),
            (_user(), ADMIN_GROUP),
            (_user(role="admin"), OTHER_TEAM_GROUP),
        ]:
            flag = await may_manage_team_policy(caller, group)
            allowed = await _may_change_policy_membership(caller, group, [MEMBER, OWNER])
            if not flag:
                assert allowed is False, (caller.id, group)
