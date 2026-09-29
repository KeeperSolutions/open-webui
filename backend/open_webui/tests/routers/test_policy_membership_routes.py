"""The group membership add/remove routes, open to team owners.

Both routes depend on `get_verified_user`, so authorisation rests entirely on the
`authorise_policy_membership_change` call. These tests pin that the call exists,
runs first, and is present on both routes. `add` and `remove` are tested
separately so a guard missing from either route fails its own tests.
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
        request=MagicMock(),
        id=group_id,
        form_data=GroupMembershipForm(user_ids=user_ids, reason=reason),
        user=user,
        db=session,
    )


async def _remove(group_id, user_ids, user, session, reason=None):
    from open_webui.routers import groups as groups_router

    return await groups_router.remove_users_from_group(
        request=MagicMock(),
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
        """An administrator may add anyone to any group."""
        await _add(ADMIN_GROUP, [STRANGER], _user(role="admin", uid="admin-1"), env)
        assert STRANGER in await _members(env, ADMIN_GROUP)

    @pytest.mark.asyncio
    async def test_a_refused_add_writes_no_audit_row(self, env):
        with pytest.raises(HTTPException):
            await _add(TEAM_GROUP, [STRANGER], _user(), env)
        assert await _audit_rows(env) == []


# ---------------------------------------------------------------------------
# /users/remove: the same tests on the other route
# ---------------------------------------------------------------------------


class TestRemoveIsGuarded:
    """A parallel class rather than a parametrised one, so each route fails on its own."""

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
        """A refused removal of a real group member writes no audit row.

        The stranger is in the group but not the team. A non-member is filtered out
        of the audit anyway, so only a real member detects a guard placed after the
        audit write.
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
        """`Groups.remove_users_from_group` refuses a removal without a reason.

        OAuth and SCIM bypass the route and its 400, so the model's own check is
        what protects them.
        """
        from open_webui.models.groups import Groups

        assert await Groups.remove_users_from_group(TEAM_GROUP, [MEMBER], db=env) is None
        assert MEMBER in await _members(env, TEAM_GROUP)


# ---------------------------------------------------------------------------
# An empty body
# ---------------------------------------------------------------------------


class TestAnEmptyBodyShortCircuits:
    """An empty body returns without calling the guard at all.

    The guard refuses empty targets, so the route must short-circuit first. The
    tripwire detects a route that calls the guard and ignores its answer.
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
        """With no targets, authorisation is not consulted, so even a stranger gets a no-op."""
        fn = _add if call == "add" else _remove
        assert await fn(ADMIN_GROUP, [], _user(uid=STRANGER), env) is None


# ---------------------------------------------------------------------------
# Where the guard sits, structurally
# ---------------------------------------------------------------------------


def test_the_guard_runs_before_anything_else_on_both_routes():
    """The guard call precedes the audit write and member lookups in both routes.

    Checked from source because the ordering must also hold on the allowed path,
    where behaviour leaves nothing to inspect.
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
    """Both routes use `get_verified_user`, not `get_admin_user`.

    A leftover `get_admin_user` would refuse owners by itself and hide a guard
    that does nothing.
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
# A team's group accepts only team members, even from an admin
# ---------------------------------------------------------------------------


class TestTheTeamGroupHoldsOnlyTheTeam:
    """A team's group membership is limited to team members, like its name and permissions.

    An outsider added by an administrator would be invisible on the owner's
    dashboard and unremovable by the owner, since the guard refuses targets
    outside the team.
    """

    @pytest.mark.asyncio
    async def test_an_admin_cannot_add_an_outsider_to_a_team_group(self, env):
        with pytest.raises(HTTPException) as raised:
            await _add(TEAM_GROUP, [STRANGER], _user(role="admin", uid="admin-1"), env)
        assert raised.value.status_code == 400

    @pytest.mark.asyncio
    async def test_and_no_audit_row_is_written_for_the_refusal(self, env):
        """The route refuses before auditing, so no `member_added` row is written.

        `Groups.add_users_to_group` also refuses, but only after the route has
        audited, which would leave a row for a change that never happened.
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
        """A guard that refused every group would pass the tests above, so this pins
        that ordinary groups still accept anyone."""
        await _add(ADMIN_GROUP, [STRANGER], _user(role="admin", uid="admin-1"), env)
        assert await _members(env, ADMIN_GROUP) == {STRANGER}

    @pytest.mark.asyncio
    async def test_a_mixed_request_is_refused_whole(self, env):
        """One outsider refuses the whole request, so no partial change is applied."""
        with pytest.raises(HTTPException):
            await _add(TEAM_GROUP, [OWNER, STRANGER], _user(role="admin", uid="admin-1"), env)
        assert await _members(env, TEAM_GROUP) == {MEMBER}

    @pytest.mark.asyncio
    async def test_the_model_refuses_it_too_for_callers_that_skip_the_route(self, env):
        """SCIM and OAuth bypass the route, so the model enforces the rule too.

        Asserted separately so that removing either copy fails a test.
        """
        from open_webui.models.groups import Groups

        assert await Groups.add_users_to_group(TEAM_GROUP, [STRANGER], db=env) is None
        assert await _members(env, TEAM_GROUP) == {MEMBER}
        assert await Groups.set_group_user_ids_by_id(
            TEAM_GROUP, [MEMBER, STRANGER], reason="sync", db=env
        ) is False
        assert await _members(env, TEAM_GROUP) == {MEMBER}
