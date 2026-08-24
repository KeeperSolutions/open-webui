"""G-C2 — who may change membership of a team's PII policy group.

⚠️ Two checks, and the whole point of this file is that **neither covers the
other**. A guard that is shadowed by a second guard behaves identically to a
guard that works, right up until the second one is removed or reordered — so the
cases below are deliberately non-overlapping, and each check is killed by a
mutation that touches only its own tests. Same shape as D1/D2 in level A.

The guard is wired to no route yet, and `test_no_route_calls_the_guard_yet`
keeps it that way. A guard introduced together with its caller cannot be shown
to be the thing doing the refusing — the route has its own reasons to say no.
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
    """Counts statements at the cursor.

    ⚠️ Counted here rather than inferred from the shape of the code. The guard
    runs once per request, before every membership change, so its cost has to be
    a measurement before a route starts paying it — and "looks like two queries"
    is not a measurement.
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
        # ⚠️ `role="member"` for EVERYONE, including the two owners, and that is
        # the point rather than an oversight. `teams.owner_user_id` and
        # `team_members.role` are written independently when a team is created
        # and nothing keeps them in step afterwards, so the fixture encodes them
        # DISAGREEING. A guard that read ownership off the role would pass every
        # test here only if the two happened to agree.
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

    # ⚠️ BOTH names are patched, and the second one is not optional.
    #
    # `team_ownership_of_group` imports the context manager function-locally, so
    # it sees `internal.db`; `models.billing` imported it at module scope, so it
    # holds its own reference and the first patch never reaches it. Patching only
    # the first sent check 2 to a real database — the failure surfaced here as
    # "no such table: team_members", which is the lucky version. Under a
    # DATABASE_URL that DID have the table, every check-2 test would have passed
    # against somebody else's data.
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
    """They are a member of their own team, so nothing special happens here."""
    assert await _may_change_policy_membership(_user(), TEAM_GROUP, [OWNER]) is True


@pytest.mark.asyncio
async def test_an_owner_may_act_on_several_of_their_own(env):
    assert await _may_change_policy_membership(_user(), TEAM_GROUP, [OWNER, MEMBER]) is True


# ---------------------------------------------------------------------------
# Check 1 — the GROUP must be their own team's policy
# ---------------------------------------------------------------------------


class TestTheGroupMustBeTheirs:
    """⚠️ Every case here is arranged so that check 2 would PASS.

    That is what makes them check-1 tests, and it is not automatic. The first
    version of `test_another_teams_policy_is_refused` named this owner's own
    member as the target — and against another team's group, check 2 refuses that
    on its own. The test would have gone on passing with the ownership comparison
    deleted, which is precisely the failure this file exists to rule out.

    The target therefore has to be a member of the team that owns the group being
    reached for.
    """

    @pytest.mark.asyncio
    async def test_another_teams_policy_is_refused(self, env):
        # ⚠️ `OTHER_MEMBER`, not `MEMBER`: the target must belong to the OTHER
        # team, or check 2 refuses this and check 1 is never the reason.
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
# Check 2 — every TARGET must be a member of that team
# ---------------------------------------------------------------------------


class TestEveryTargetMustBeTheirs:
    """⚠️ Every case here uses the owner's OWN policy group.

    That is what makes them check-2 tests: with check 1 satisfied, only check 2
    can be doing the refusing.
    """

    @pytest.mark.asyncio
    async def test_a_stranger_is_refused(self, env):
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [STRANGER]) is False

    @pytest.mark.asyncio
    async def test_another_teams_member_is_refused(self, env):
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [OTHER_MEMBER]) is False

    @pytest.mark.asyncio
    async def test_a_mixed_request_is_refused_whole(self, env):
        """⚠️ Not partially authorised, and not silently trimmed.

        Authorising the allowed half would let an owner learn who exists outside
        their team by watching which halves succeed.
        """
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [MEMBER, STRANGER]) is False

    @pytest.mark.asyncio
    async def test_a_duplicated_member_is_still_allowed(self, env):
        assert await _may_change_policy_membership(_user(), TEAM_GROUP, [MEMBER, MEMBER]) is True


# ---------------------------------------------------------------------------
# An empty request
# ---------------------------------------------------------------------------


class TestAnEmptyRequestFailsClosed:
    """⚠️ The fail-open this guard would otherwise have.

    "Nobody named, so nobody to refuse" makes check 2 pass vacuously: the set of
    targets that are not members of the team is empty too. The same shape as
    `models/users.py:456`, where an empty `user_ids` filter meant "no filter" and
    so "every user", until it was closed by returning nothing.

    Closed for EVERY role, before the role is consulted — the guard answers "may
    this caller change membership for these people", and with nobody named there
    is no honest yes. A caller that wants an empty body to be a no-op says so
    itself, before asking.
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
# Cost — measured, not assumed
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
        """⚠️ One query for the whole body, not one per person.

        The guard runs before every membership change, and the dashboard can name
        a whole team at once.
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
# Nothing calls it yet
# ---------------------------------------------------------------------------


def test_no_route_calls_the_guard_yet():
    """⚠️ Routers go through `authorise_policy_membership_change`, never through this.

    Written in G-C2, when nothing called the guard at all, to keep that gate's
    mutations from being caught by a route refusing for its own reasons. G-C3
    wired the routes up and the test survived unchanged, because what it pins
    turned out to be worth keeping permanently:

    the boolean is reachable only through the function that RAISES. A router that
    imported it directly would write `if not await guard(...)`, and that is one
    missing `not` away from failing open, on the two routes in this feature that
    can fail open at all.
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
    """⚠️ Tested directly, because the guard cannot reach all of it.

    Found by a mutation that SURVIVED: deleting the empty-list short circuit in
    `members_among` broke nothing, since the guard refuses an empty request
    before ever calling it. That makes the short circuit unreachable from here —
    but `members_among` is a model method, and the next caller need not have a
    guard in front of it.
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
# G-C4 — check 1 on its own, as the response flag
# ---------------------------------------------------------------------------


class TestMayManageTeamPolicy:
    """The flag the directory reports. Check 1, and only check 1.

    ⚠️ Tested here rather than only through the route, because the case that
    matters most cannot reach the route at all: `_may_read_team_dashboard` admits
    an administrator or the team's owner and refuses everyone else, so a plain
    member never gets a scope to carry a flag on.

    That is exactly why the flag must not be derived from "is this view
    team-scoped". Today the two agree; the day that guard widens by an `or` — the
    way its own docstring anticipates — the proxy hands a plain member the
    owner's power, and every test that went through the route keeps passing.
    """

    @pytest.mark.asyncio
    async def test_the_owner_may(self, env):
        assert await may_manage_team_policy(_user(), TEAM_GROUP) is True

    @pytest.mark.asyncio
    async def test_an_admin_may_anywhere(self, env):
        assert await may_manage_team_policy(_user(role="admin"), OTHER_TEAM_GROUP) is True

    @pytest.mark.asyncio
    async def test_a_plain_member_may_not(self, env):
        """⚠️ The anti-proxy test. Unreachable through the route, by design."""
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
        """⚠️ Check 1 alone, so half the cost of the full guard — and it must stay
        half. A flag that also asked about targets would pay for a question the
        page cannot answer."""
        counter = env
        await may_manage_team_policy(_user(), TEAM_GROUP)
        assert counter.selects == 1

    @pytest.mark.asyncio
    async def test_it_agrees_with_the_guard_wherever_the_guard_has_targets(self, env):
        """One source, two entries: `team_ownership_of_group` and `_governs`.

        Where the flag says no, the guard must say no too — otherwise a screen
        that hides the button protects nothing, or one that shows it lies.
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
