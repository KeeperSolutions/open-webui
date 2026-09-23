"""Leaving a team removes the person from the team's PII policy group.

Pinned: no-op cases write no audit row, since the table records transitions;
the audit row is written first and a failed write blocks the removal; the system
actor constants have one definition, which the migrations' copies must match.
"""

import ast
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from open_webui.models.billing import Team, TeamMember, TeamMembers
from open_webui.models.groups import Group, GroupMember, Groups
from open_webui.models.pii_policy_audit import (
    EVENT_MEMBER_REMOVED,
    PiiPolicyAudit,
    PiiPolicyAudits,
    REASON_LEFT_TEAM,
    SYSTEM_ACTOR_EMAIL,
    SYSTEM_ACTOR_ID,
)
from open_webui.utils.team_groups import (
    TEAM_PII_GROUP_PERMISSIONS,
    remove_from_team_policy_group,
)


TEAM, BARE_TEAM = "t-with-policy", "t-without-policy"
TEAM_GROUP = "g-team-pii"

IN_POLICY = "u-in-policy"
OUT_OF_POLICY = "u-out-of-policy"
BARE_TEAM_MEMBER = "u-bare"

BACKEND = Path(__file__).resolve().parents[3]


@pytest_asyncio.fixture
async def env():
    """One team with a policy group and one without, each with members.

    `OUT_OF_POLICY` is in the same team as `IN_POLICY`, so a wrong team lookup
    cannot pass for a correct membership check.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (Team, TeamMember, Group, GroupMember, PiiPolicyAudit):
            await conn.run_sync(table.__table__.create, checkfirst=True)

    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())

    def team(team_id, group_id):
        return Team(
            id=team_id,
            name=team_id,
            owner_user_id="u-owner",
            seat_limit=10,
            monthly_credits=0,
            group_id=group_id,
            created_at=now,
            updated_at=now,
        )

    def member(team_id, user_id):
        return TeamMember(
            id=f"tm-{team_id}-{user_id}",
            team_id=team_id,
            user_id=user_id,
            role="member",
            created_at=now,
        )

    session.add_all(
        [
            team(TEAM, TEAM_GROUP),
            # A team without a policy group is a normal state and must be a
            # no-op, not an error.
            team(BARE_TEAM, None),
            member(TEAM, IN_POLICY),
            member(TEAM, OUT_OF_POLICY),
            member(BARE_TEAM, BARE_TEAM_MEMBER),
            Group(
                id=TEAM_GROUP,
                user_id="u-owner",
                name="PII — team",
                description="",
                # The real enforcing flag: `remove_users_from_group` refuses a
                # removal from an enforcing group without a reason.
                permissions=TEAM_PII_GROUP_PERMISSIONS,
                created_at=now,
                updated_at=now,
            ),
            GroupMember(
                id="gm-1", group_id=TEAM_GROUP, user_id=IN_POLICY, created_at=now, updated_at=now
            ),
        ]
    )
    await session.commit()

    @asynccontextmanager
    async def _ctx(db=None):
        yield session

    # Every module holding its own module-scope reference to the context manager
    # is patched by name; an unpatched one would query the real database.
    with patch("open_webui.internal.db.get_async_db_context", _ctx), patch(
        "open_webui.models.billing.get_async_db_context", _ctx
    ), patch("open_webui.models.groups.get_async_db_context", _ctx), patch(
        "open_webui.models.pii_policy_audit.get_async_db_context", _ctx
    ):
        yield session

    await session.close()
    await engine.dispose()


async def _audit_rows(session):
    from sqlalchemy import select

    result = await session.execute(select(PiiPolicyAudit))
    return list(result.scalars().all())


async def _group_members(session):
    return set(await Groups.get_group_user_ids_by_id(TEAM_GROUP))


# ---------------------------------------------------------------------------
# The move itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_member_of_the_policy_is_taken_out_of_it(env):
    assert await remove_from_team_policy_group(TEAM, IN_POLICY) is True
    assert await _group_members(env) == set()


@pytest.mark.asyncio
async def test_it_writes_exactly_one_audit_row(env):
    await remove_from_team_policy_group(TEAM, IN_POLICY)
    rows = await _audit_rows(env)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_the_row_names_the_system_as_the_actor(env):
    await remove_from_team_policy_group(TEAM, IN_POLICY)
    (row,) = await _audit_rows(env)
    assert row.event_type == EVENT_MEMBER_REMOVED
    assert row.actor_user_id == SYSTEM_ACTOR_ID
    assert row.actor_email == SYSTEM_ACTOR_EMAIL
    assert row.user_id == IN_POLICY
    assert row.group_id == TEAM_GROUP


@pytest.mark.asyncio
async def test_the_row_carries_a_reason(env):
    await remove_from_team_policy_group(TEAM, IN_POLICY)
    (row,) = await _audit_rows(env)
    assert (row.reason or "").strip()


def test_the_reason_names_the_cause_rather_than_the_gesture():
    """The reason says the person left the team, not just that they were removed.

    A reason that restates `member_removed` reads as protection being taken away.
    """
    assert "no longer a member of the team" in REASON_LEFT_TEAM
    assert "team" in REASON_LEFT_TEAM.lower()
    # It has to say more than the event type already says on its own.
    assert len(REASON_LEFT_TEAM.split()) > 6


# ---------------------------------------------------------------------------
# The no-ops, checked by counting audit rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_someone_outside_the_policy_is_not_acted_on(env):
    assert await remove_from_team_policy_group(TEAM, OUT_OF_POLICY) is False


@pytest.mark.asyncio
async def test_someone_outside_the_policy_leaves_no_audit_row(env):
    """A no-op writes no audit row.

    Separate from the return-value test, because writing a row and removing
    nobody also returns `False`.
    """
    await remove_from_team_policy_group(TEAM, OUT_OF_POLICY)
    assert await _audit_rows(env) == []


@pytest.mark.asyncio
async def test_someone_outside_the_policy_does_not_disturb_its_members(env):
    await remove_from_team_policy_group(TEAM, OUT_OF_POLICY)
    assert await _group_members(env) == {IN_POLICY}


@pytest.mark.asyncio
async def test_a_team_with_no_policy_group_is_a_no_op(env):
    assert await remove_from_team_policy_group(BARE_TEAM, BARE_TEAM_MEMBER) is False


@pytest.mark.asyncio
async def test_a_team_with_no_policy_group_leaves_no_audit_row(env):
    await remove_from_team_policy_group(BARE_TEAM, BARE_TEAM_MEMBER)
    assert await _audit_rows(env) == []


@pytest.mark.asyncio
async def test_a_team_with_no_policy_group_never_asks_who_is_in_one(env):
    """A team with no `group_id` returns before looking up group members.

    Without that check, looking up the members of `None` returns an empty list
    and gives the same answer by accident, so the call itself is observed.
    """
    asked = []

    real = Groups.get_group_user_ids_by_id

    async def _watched(self_or_id, *args, **kwargs):
        asked.append(self_or_id)
        return await real(self_or_id, *args, **kwargs)

    with patch.object(Groups, "get_group_user_ids_by_id", _watched):
        assert await remove_from_team_policy_group(BARE_TEAM, BARE_TEAM_MEMBER) is False

    assert asked == [], f"asked for the members of {asked!r}"


@pytest.mark.asyncio
async def test_an_unknown_team_is_a_no_op(env):
    assert await remove_from_team_policy_group("t-does-not-exist", IN_POLICY) is False
    assert await _audit_rows(env) == []


# ---------------------------------------------------------------------------
# Order, and the direction of failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_audit_write_leaves_the_membership_alone(env):
    """A failed audit write blocks the removal, for the system actor too.

    `test_a_failed_audit_write_happens_before_the_removal` separately rules out
    removing first and rolling back.
    """
    with patch.object(
        PiiPolicyAudits, "insert_event", side_effect=RuntimeError("audit is down")
    ):
        with pytest.raises(RuntimeError):
            await remove_from_team_policy_group(TEAM, IN_POLICY)

    assert await _group_members(env) == {IN_POLICY}


@pytest.mark.asyncio
async def test_a_failed_removal_is_not_reported_as_a_successful_one(env):
    """A `None` from `remove_users_from_group` raises instead of returning `True`.

    That method turns database errors into `None`. Reporting success would hide
    the audit row that has no matching removal. It raises rather than returning
    `False`, because `False` means there was nothing to do.
    """
    from open_webui.models.groups import Groups

    with patch.object(Groups, "remove_users_from_group", return_value=None):
        with pytest.raises(RuntimeError):
            await remove_from_team_policy_group(TEAM, IN_POLICY)

    # The person keeps their masking, and the unmatched audit row stays visible.
    assert await _group_members(env) == {IN_POLICY}
    assert len(await _audit_rows(env)) == 1


@pytest.mark.asyncio
async def test_a_failed_audit_write_happens_before_the_removal(env):
    """Once the audit write fails, `remove_users_from_group` is never called."""
    order = []

    async def _failing_insert(*args, **kwargs):
        order.append("audit")
        raise RuntimeError("audit is down")

    real_remove = Groups.remove_users_from_group

    async def _watched_remove(*args, **kwargs):
        order.append("remove")
        return await real_remove(*args, **kwargs)

    with patch.object(PiiPolicyAudits, "insert_event", _failing_insert), patch.object(
        Groups, "remove_users_from_group", _watched_remove
    ):
        with pytest.raises(RuntimeError):
            await remove_from_team_policy_group(TEAM, IN_POLICY)

    assert order == ["audit"]


@pytest.mark.asyncio
async def test_the_audit_row_precedes_the_removal_when_both_succeed(env):
    order = []

    real_insert = PiiPolicyAudits.insert_event
    real_remove = Groups.remove_users_from_group

    async def _watched_insert(*args, **kwargs):
        order.append("audit")
        return await real_insert(*args, **kwargs)

    async def _watched_remove(*args, **kwargs):
        order.append("remove")
        return await real_remove(*args, **kwargs)

    with patch.object(PiiPolicyAudits, "insert_event", _watched_insert), patch.object(
        Groups, "remove_users_from_group", _watched_remove
    ):
        await remove_from_team_policy_group(TEAM, IN_POLICY)

    assert order == ["audit", "remove"]


# ---------------------------------------------------------------------------
# Rejoining the team does not restore policy membership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejoining_the_team_does_not_put_them_back_in_the_policy(env):
    """Rejoining a team does not re-add the person to its policy group.

    Pinned so that adding that behaviour later is a deliberate change.
    """
    await remove_from_team_policy_group(TEAM, IN_POLICY)
    assert await _group_members(env) == set()

    env.add(
        TeamMember(
            id="tm-rejoin",
            team_id=TEAM,
            user_id=IN_POLICY,
            role="member",
            created_at=int(time.time()),
        )
    )
    await env.commit()

    assert await _group_members(env) == set()


# ---------------------------------------------------------------------------
# The constants have one home
# ---------------------------------------------------------------------------


def _module_constant(path: Path, name: str) -> str:
    """Reads a literal assignment out of a file without importing it.

    Alembic revisions cannot be imported by name here, and importing one for its
    constants would drag in the whole migration environment.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


MIGRATIONS = BACKEND / "open_webui" / "migrations" / "versions"
SEED_MIGRATION = MIGRATIONS / "1782400007_seed_pii_policy_group.py"
BRIDGE_MIGRATION = MIGRATIONS / "b6d1a4f0c7e2_bridge_team_pii_groups.py"


@pytest.mark.parametrize("migration", [SEED_MIGRATION, BRIDGE_MIGRATION])
@pytest.mark.parametrize(
    "name,expected",
    [("SYSTEM_ACTOR_ID", SYSTEM_ACTOR_ID), ("SYSTEM_ACTOR_EMAIL", SYSTEM_ACTOR_EMAIL)],
)
def test_the_migrations_still_agree_with_the_home_of_the_actor(migration, name, expected):
    """The migrations' copies of the system actor match the model constants.

    Applied revisions cannot be edited, so their copies are checked instead.
    Differing values would split the audit trail into two system actors.
    """
    assert _module_constant(migration, name) == expected


def test_new_writers_do_not_spell_the_system_actor_themselves():
    """Non-migration code spells the system actor only via `models/pii_policy_audit.py`.

    Migrations are excluded; their copies are checked by the test above.
    """
    offenders = []
    for path in (BACKEND / "open_webui").rglob("*.py"):
        rel = path.relative_to(BACKEND / "open_webui").as_posix()
        if rel.startswith(("migrations/", "tests/")) or rel == "models/pii_policy_audit.py":
            continue
        if "system@open-webui" in path.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert offenders == [], f"spell the actor via models/pii_policy_audit.py instead: {offenders}"


# ---------------------------------------------------------------------------
# The route actually makes the move
# ---------------------------------------------------------------------------


def _remove_team_member_ast():
    source = (BACKEND / "open_webui" / "routers" / "billing.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "remove_team_member":
            return node
    raise AssertionError("remove_team_member is gone from routers/billing.py")


def test_the_route_calls_the_move():
    """`remove_team_member` calls `remove_from_team_policy_group`.

    Checked from source because running the route needs Stripe, billing config
    and a request.
    """
    called = {
        node.func.id
        for node in ast.walk(_remove_team_member_ast())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "remove_from_team_policy_group" in called


def test_the_route_still_takes_no_db_parameter():
    """`remove_team_member` takes no `db` parameter.

    The policy-group removal manages its own sessions, and callers do not pass one.
    """
    node = _remove_team_member_ast()
    names = [a.arg for a in node.args.args + node.args.kwonlyargs]
    assert "db" not in names, names


# ---------------------------------------------------------------------------
# The move survives a failure of the billing revert
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _removal_route(revert_raises=False):
    """`remove_team_member` with its collaborators replaced.

    Yields the mock standing in for the policy-group removal, so a test can ask
    whether the route reached it.
    """
    from open_webui.routers import billing as route_mod

    team = SimpleNamespace(id="T1", owner_user_id="owner")
    move = AsyncMock()
    revert = AsyncMock(side_effect=RuntimeError("billing is down") if revert_raises else None)
    with patch.object(route_mod, "require_billing_enabled", MagicMock()), patch.object(
        route_mod.Teams, "get_by_owner_user_id", AsyncMock(return_value=team)
    ), patch.object(
        route_mod.TeamMembers, "remove", AsyncMock(return_value=True)
    ), patch.object(
        route_mod.StripeBillings, "revert_to_trial", revert
    ), patch.object(
        route_mod, "remove_from_team_policy_group", move
    ):
        yield move


@pytest.mark.asyncio
async def test_the_member_is_taken_out_of_the_policy_when_the_revert_fails():
    """The membership row is already committed when the billing revert runs.

    Skipping the policy removal would leave someone in the team's policy group
    who is no longer in the team, and only team members may be taken out of it,
    so the owner could not remove them afterwards.
    """
    from open_webui.routers.billing import remove_team_member

    async with _removal_route(revert_raises=True) as move:
        with pytest.raises(RuntimeError):
            await remove_team_member("u-member", user=SimpleNamespace(id="owner"))
        move.assert_awaited_once_with("T1", "u-member")


@pytest.mark.asyncio
async def test_a_failed_revert_is_still_reported_as_a_failure():
    """The request fails; the caller is not told the member was removed."""
    from open_webui.routers.billing import remove_team_member

    async with _removal_route(revert_raises=True):
        with pytest.raises(RuntimeError):
            await remove_team_member("u-member", user=SimpleNamespace(id="owner"))


@pytest.mark.asyncio
async def test_an_ordinary_removal_still_reports_success():
    from open_webui.routers.billing import remove_team_member

    async with _removal_route() as move:
        assert await remove_team_member("u-member", user=SimpleNamespace(id="owner")) == {
            "removed": True
        }
        move.assert_awaited_once_with("T1", "u-member")


@pytest.mark.asyncio
async def test_an_unknown_member_never_reaches_the_policy_group():
    """A 404 answers before the removal, so nothing is written for a stranger."""
    from open_webui.routers import billing as route_mod
    from open_webui.routers.billing import remove_team_member
    from fastapi import HTTPException

    move = AsyncMock()
    with patch.object(route_mod, "require_billing_enabled", MagicMock()), patch.object(
        route_mod.Teams, "get_by_owner_user_id",
        AsyncMock(return_value=SimpleNamespace(id="T1", owner_user_id="owner")),
    ), patch.object(
        route_mod.TeamMembers, "remove", AsyncMock(return_value=False)
    ), patch.object(
        route_mod.StripeBillings, "revert_to_trial", AsyncMock()
    ), patch.object(route_mod, "remove_from_team_policy_group", move):
        with pytest.raises(HTTPException) as raised:
            await remove_team_member("u-stranger", user=SimpleNamespace(id="owner"))

    assert raised.value.status_code == 404
    move.assert_not_awaited()
