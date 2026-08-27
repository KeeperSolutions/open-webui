"""G-C3 — the two membership routes, opened to a team owner.

⚠️ This is the only change in level C that can fail OPEN. Both routes carried
`get_admin_user`; they now carry `get_verified_user` and an authorisation call.
Everything that used to be enforced by the dependency is enforced by one named
call on the first line, so these tests are about that call being there, being
first, and being on BOTH routes.

⚠️ `add` and `remove` are tested separately throughout, and no test exercises
both. One guard on one of the two routes behaves exactly like a guard on both,
right up until somebody calls the other one — so the gate's mutation table pairs
each route with a mutation that kills only its own tests. Same shape as D1/D2 in
level A.
"""

import sys
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from fastapi import HTTPException

from open_webui.models.billing import Team, TeamMember
from open_webui.models.groups import Group, GroupMember, GroupMembershipForm
from open_webui.models.pii_policy_audit import PiiPolicyAudit
from open_webui.models.users import User


OWNER, MEMBER, STRANGER = "u-owner", "u-member", "u-stranger"
TEAM, TEAM_GROUP = "t1", "g-team-pii"
ADMIN_GROUP = "pii-masking-policy"
ENFORCING = {"chat": {"pii_masking_enforced": True}}


def _user(role="user", uid=OWNER):
    return MagicMock(id=uid, role=role, email=f"{uid}@x.com")


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (Team, TeamMember, Group, GroupMember, PiiPolicyAudit, User):
            await conn.run_sync(table.__table__.create, checkfirst=True)

    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())

    session.add_all(
        [
            Team(
                id=TEAM,
                name="Acme",
                owner_user_id=OWNER,
                seat_limit=10,
                monthly_credits=0,
                group_id=TEAM_GROUP,
                created_at=now,
                updated_at=now,
            ),
            TeamMember(id="tm-1", team_id=TEAM, user_id=OWNER, role="owner", created_at=now),
            TeamMember(id="tm-2", team_id=TEAM, user_id=MEMBER, role="member", created_at=now),
            Group(
                id=TEAM_GROUP,
                user_id="",
                name="PII — Acme · t1",
                description="",
                data={},
                meta=None,
                permissions=ENFORCING,
                created_at=now,
                updated_at=now,
            ),
            Group(
                id=ADMIN_GROUP,
                user_id="admin",
                name="PII Masking Policy",
                description="",
                data={},
                meta=None,
                permissions=ENFORCING,
                created_at=now,
                updated_at=now,
            ),
            GroupMember(id="gm-1", group_id=TEAM_GROUP, user_id=MEMBER, created_at=now, updated_at=now),
        ]
    )
    for uid in (OWNER, MEMBER, STRANGER):
        session.add(
            User(
                id=uid,
                name=uid,
                email=f"{uid}@x.com",
                role="user",
                profile_image_url="",
                last_active_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    await session.commit()

    @asynccontextmanager
    async def _ctx(db=None):
        yield session

    # Every module that opened its own reference to the context manager.
    with patch("open_webui.internal.db.get_async_db_context", _ctx), patch(
        "open_webui.models.groups.get_async_db_context", _ctx
    ), patch("open_webui.models.billing.get_async_db_context", _ctx), patch(
        "open_webui.models.users.get_async_db_context", _ctx
    ), patch(
        "open_webui.models.pii_policy_audit.get_async_db_context", _ctx
    ):
        yield session

    await session.close()
    await engine.dispose()


async def _add(group_id, user_ids, user, session, reason=None):
    from open_webui.routers import groups as groups_router

    return await groups_router.add_user_to_group(
        id=group_id,
        form_data=GroupMembershipForm(user_ids=user_ids, reason=reason),
        user=user,
        db=session,
    )


async def _remove(group_id, user_ids, user, session, reason=None):
    from open_webui.routers import groups as groups_router

    return await groups_router.remove_users_from_group(
        id=group_id,
        form_data=GroupMembershipForm(user_ids=user_ids, reason=reason),
        user=user,
        db=session,
    )


async def _members(session, group_id):
    result = await session.execute(
        select(GroupMember.user_id).filter(GroupMember.group_id == group_id)
    )
    return {uid for (uid,) in result.all()}


async def _audit_rows(session):
    result = await session.execute(select(PiiPolicyAudit))
    return result.scalars().all()


# ---------------------------------------------------------------------------
# /users/add
# ---------------------------------------------------------------------------


class TestAddIsGuarded:
    @pytest.mark.asyncio
    async def test_an_owner_may_add_their_own_member(self, env):
        await _add(TEAM_GROUP, [OWNER], _user(), env)
        assert OWNER in await _members(env, TEAM_GROUP)

    @pytest.mark.asyncio
    async def test_an_owner_may_not_add_a_stranger(self, env):
        with pytest.raises(HTTPException) as exc:
            await _add(TEAM_GROUP, [STRANGER], _user(), env)
        assert exc.value.status_code == 401
        assert STRANGER not in await _members(env, TEAM_GROUP)

    @pytest.mark.asyncio
    async def test_an_owner_may_not_reach_an_administrators_group(self, env):
        with pytest.raises(HTTPException) as exc:
            await _add(ADMIN_GROUP, [MEMBER], _user(), env)
        assert exc.value.status_code == 401
        assert await _members(env, ADMIN_GROUP) == set()

    @pytest.mark.asyncio
    async def test_a_plain_member_may_not_add_anyone(self, env):
        with pytest.raises(HTTPException):
            await _add(TEAM_GROUP, [OWNER], _user(uid=MEMBER), env)
        assert OWNER not in await _members(env, TEAM_GROUP)

    @pytest.mark.asyncio
    async def test_an_admin_may_still_add_anyone_anywhere(self, env):
        """The dependency changed; the administrator's reach did not."""
        await _add(ADMIN_GROUP, [STRANGER], _user(role="admin", uid="admin-1"), env)
        assert STRANGER in await _members(env, ADMIN_GROUP)

    @pytest.mark.asyncio
    async def test_a_refused_add_writes_no_audit_row(self, env):
        with pytest.raises(HTTPException):
            await _add(TEAM_GROUP, [STRANGER], _user(), env)
        assert await _audit_rows(env) == []


# ---------------------------------------------------------------------------
# /users/remove — the same tests, on the other route
# ---------------------------------------------------------------------------


class TestRemoveIsGuarded:
    """⚠️ Deliberately a parallel class rather than a parametrised one.

    A parametrised suite over both routes would go green with a guard on either
    of them, because a shared failure is indistinguishable from two.
    """

    @pytest.mark.asyncio
    async def test_an_owner_may_remove_their_own_member(self, env):
        await _remove(TEAM_GROUP, [MEMBER], _user(), env, reason="left the pilot")
        assert MEMBER not in await _members(env, TEAM_GROUP)

    @pytest.mark.asyncio
    async def test_an_owner_may_not_remove_a_stranger(self, env):
        with pytest.raises(HTTPException) as exc:
            await _remove(TEAM_GROUP, [STRANGER], _user(), env, reason="because")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_an_owner_may_not_reach_an_administrators_group(self, env):
        with pytest.raises(HTTPException) as exc:
            await _remove(ADMIN_GROUP, [MEMBER], _user(), env, reason="because")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_a_plain_member_may_not_remove_anyone(self, env):
        with pytest.raises(HTTPException):
            await _remove(TEAM_GROUP, [MEMBER], _user(uid=MEMBER), env, reason="because")
        assert MEMBER in await _members(env, TEAM_GROUP)

    @pytest.mark.asyncio
    async def test_an_admin_may_still_remove_anyone(self, env):
        await _remove(TEAM_GROUP, [MEMBER], _user(role="admin", uid="admin-1"), env, reason="tidy")
        assert MEMBER not in await _members(env, TEAM_GROUP)

    @pytest.mark.asyncio
    async def test_a_refused_removal_writes_no_audit_row(self, env):
        """⚠️ The stranger has to be IN the group, and that is the whole test.

        Found by a mutation that only half died: moving the guard below the audit
        broke the `add` version of this test and not the `remove` one, because a
        stranger who is not a member is filtered out of the audit anyway. The
        route would have recorded a removal it then refused — and nothing said so.

        So the target here is a member of the group but not of the team: check 2
        refuses, and there is a real row for the ordering to get wrong.
        """
        env.add(
            GroupMember(
                id="gm-outsider",
                group_id=TEAM_GROUP,
                user_id=STRANGER,
                created_at=0,
                updated_at=0,
            )
        )
        await env.commit()

        with pytest.raises(HTTPException) as exc:
            await _remove(TEAM_GROUP, [STRANGER], _user(), env, reason="because")

        assert exc.value.status_code == 401
        assert STRANGER in await _members(env, TEAM_GROUP)
        assert await _audit_rows(env) == []


# ---------------------------------------------------------------------------
# The reason still reaches the model
# ---------------------------------------------------------------------------


class TestTheReasonSurvivesTheNewCaller:
    @pytest.mark.asyncio
    async def test_an_owner_removing_without_a_reason_is_refused(self, env):
        with pytest.raises(HTTPException) as exc:
            await _remove(TEAM_GROUP, [MEMBER], _user(), env)
        assert exc.value.status_code == 400
        assert MEMBER in await _members(env, TEAM_GROUP)
        assert await _audit_rows(env) == []

    @pytest.mark.asyncio
    async def test_the_model_refuses_it_too_without_the_route(self, env):
        """⚠️ On the MODEL, not the route.

        The route has its own 400 for a missing reason, so a route test proves
        the route. OAuth and SCIM never pass through it, and the guard that
        actually protects them is the one in `Groups.remove_users_from_group` —
        which is only reached here by calling it directly.
        """
        from open_webui.models.groups import Groups

        assert await Groups.remove_users_from_group(TEAM_GROUP, [MEMBER], db=env) is None
        assert MEMBER in await _members(env, TEAM_GROUP)


# ---------------------------------------------------------------------------
# An empty body
# ---------------------------------------------------------------------------


class TestAnEmptyBodyShortCircuits:
    """⚠️ It must not ASK. Returning 200 is not the property under test.

    An implementation that authorises an empty body and then ignores the answer
    passes every "it returned 200" test while making an empty request an
    authorisation event — one that the guard, correctly, refuses. The tripwire
    below is what tells the two apart.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", ["add", "remove"])
    @pytest.mark.parametrize("targets", [[], None])
    async def test_an_empty_body_never_reaches_the_guard(self, env, call, targets):
        from open_webui.routers import groups as groups_router

        asked = False

        async def _tripwire(user, group_id, target_user_ids, db=None):
            nonlocal asked
            asked = True

        with patch.object(groups_router, "authorise_policy_membership_change", _tripwire):
            fn = _add if call == "add" else _remove
            assert await fn(TEAM_GROUP, targets, _user(), env) is None

        assert asked is False, "an empty body must not be an authorisation event"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", ["add", "remove"])
    async def test_an_empty_body_writes_no_audit_row_and_changes_nothing(self, env, call):
        before = await _members(env, TEAM_GROUP)
        fn = _add if call == "add" else _remove
        assert await fn(TEAM_GROUP, [], _user(), env) is None
        assert await _members(env, TEAM_GROUP) == before
        assert await _audit_rows(env) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", ["add", "remove"])
    async def test_an_empty_body_is_short_circuited_even_for_a_stranger(self, env, call):
        """Nobody named, so nobody's authorisation is consulted — not even a refusal."""
        fn = _add if call == "add" else _remove
        assert await fn(ADMIN_GROUP, [], _user(uid=STRANGER), env) is None


# ---------------------------------------------------------------------------
# Where the guard sits, structurally
# ---------------------------------------------------------------------------


def test_the_guard_runs_before_anything_else_on_both_routes():
    """⚠️ Position, not presence. A guard that runs after the audit is not a guard.

    Read from the source rather than from behaviour, because "did the audit
    happen first" is only observable when the guard REFUSES — and the ordering
    has to hold on the allowed path too, where nothing is left behind to inspect.
    """
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2] / "routers" / "groups.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for route in ("add_user_to_group", "remove_users_from_group"):
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == route
        )
        calls = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name and name not in calls:
                    calls[name] = node.lineno

        assert "authorise_policy_membership_change" in calls, route
        guard = calls["authorise_policy_membership_change"]

        for later in ("_audit_membership_change", "get_valid_user_ids", "get_group_user_ids_by_id"):
            if later in calls:
                assert guard < calls[later], (
                    f"{route}: {later} runs at line {calls[later]}, before the guard at {guard}"
                )


def test_neither_route_still_carries_the_admin_dependency():
    """The admin-only rule moved into the guard; it did not stay in both places.

    Kept because a leftover `get_admin_user` would make every owner test above
    fail for the RIGHT reason while hiding that the guard does nothing.
    """
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2] / "routers" / "groups.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for route in ("add_user_to_group", "remove_users_from_group"):
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == route
        )
        depends = {
            arg.id
            for default in fn.args.defaults
            if isinstance(default, ast.Call)
            for arg in default.args
            if isinstance(arg, ast.Name)
        }
        assert "get_admin_user" not in depends, route
        assert "get_verified_user" in depends, route


# ---------------------------------------------------------------------------
# A team's group takes nobody from outside the team — not even from an admin
# ---------------------------------------------------------------------------


class TestTheTeamGroupHoldsOnlyTheTeam:
    """⚠️ The third derived property, and the one that was still open.

    A team's group already takes its NAME and its PERMISSIONS from the team and
    refuses to have either edited. Membership did not, so an administrator could
    add somebody who is not in the team through Groups → Add — after which the
    owner could neither SEE that person (their dashboard lists team members) nor
    remove them (`authorise_policy_membership_change` refuses targets outside the
    team). A member of the policy that nobody who owns the policy can reach.

    Found in use, not in review: an administrator did exactly this and then asked
    why the person was missing from the team dashboard.
    """

    @pytest.mark.asyncio
    async def test_an_admin_cannot_add_an_outsider_to_a_team_group(self, env):
        with pytest.raises(HTTPException) as raised:
            await _add(TEAM_GROUP, [STRANGER], _user(role="admin", uid="admin-1"), env)
        assert raised.value.status_code == 400

    @pytest.mark.asyncio
    async def test_and_no_audit_row_is_written_for_the_refusal(self, env):
        """⚠️ The load-bearing half, and the reason the guard is on the ROUTE.

        `Groups.add_users_to_group` refuses this too, but it refuses after the
        route has already recorded `member_added`. A row claiming a membership
        that was rejected is the inverted error and the worse one: a missing
        record says something is absent, a false one accuses somebody of a change
        they never made. Same lesson as the team-group edit guard.
        """
        with pytest.raises(HTTPException):
            await _add(TEAM_GROUP, [STRANGER], _user(role="admin", uid="admin-1"), env)
        assert await _audit_rows(env) == []
        assert await _members(env, TEAM_GROUP) == {MEMBER}

    @pytest.mark.asyncio
    async def test_a_team_member_is_still_added(self, env):
        """The guard is narrow: it refuses outsiders, not the action."""
        await _add(TEAM_GROUP, [OWNER], _user(role="admin", uid="admin-1"), env)
        assert await _members(env, TEAM_GROUP) == {MEMBER, OWNER}
        assert len(await _audit_rows(env)) == 1

    @pytest.mark.asyncio
    async def test_an_ordinary_group_takes_anyone(self, env):
        """⚠️ Non-overlap: a guard that refused every group would pass the two
        tests above and break the instance-wide policy group."""
        await _add(ADMIN_GROUP, [STRANGER], _user(role="admin", uid="admin-1"), env)
        assert await _members(env, ADMIN_GROUP) == {STRANGER}

    @pytest.mark.asyncio
    async def test_a_mixed_request_is_refused_whole(self, env):
        """One outsider among members refuses the request rather than admitting
        the members and dropping them — a partially applied membership change is
        the state nobody can reason about afterwards."""
        with pytest.raises(HTTPException):
            await _add(TEAM_GROUP, [OWNER, STRANGER], _user(role="admin", uid="admin-1"), env)
        assert await _members(env, TEAM_GROUP) == {MEMBER}

    @pytest.mark.asyncio
    async def test_the_model_refuses_it_too_for_callers_that_skip_the_route(self, env):
        """SCIM and OAuth never reach the route, so the model keeps its own copy.

        ⚠️ Asserted separately from the route: one guard behaves exactly like two
        until somebody calls the other door.
        """
        from open_webui.models.groups import Groups

        assert await Groups.add_users_to_group(TEAM_GROUP, [STRANGER], db=env) is None
        assert await _members(env, TEAM_GROUP) == {MEMBER}
        assert await Groups.set_group_user_ids_by_id(
            TEAM_GROUP, [MEMBER, STRANGER], reason="sync", db=env
        ) is False
        assert await _members(env, TEAM_GROUP) == {MEMBER}
