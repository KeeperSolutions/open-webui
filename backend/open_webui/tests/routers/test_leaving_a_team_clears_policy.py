"""G-C9 — leaving a team takes the person out of the team's PII policy group.

The last automatic move of level C, and the first one written outside a
migration. Three things are load-bearing here and each has its own mutation:

  * the **no-op cases leave no audit row** — this table records transitions, not
    requests, and the test that catches a regression here is the one COUNTING
    rows, not the one reading the outcome
  * the **audit is written first and blocks** — with the failure direction named
    in the code, because a mutation with no record is invisible while a record
    with no mutation is not
  * the **system actor and its reason have one home** — two literal copies exist
    already, both inside applied migrations that cannot be edited, and this file
    is what keeps those copies honest
"""

import ast
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    """One team with a policy group, one without — and somebody in each shape.

    ⚠️ `OUT_OF_POLICY` is a real member of the SAME team as `IN_POLICY`. Putting
    the two in different teams would let a mistake in the team lookup pass for a
    correct membership check.
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
            # ⚠️ `group_id=None`, not a missing team: under path B a team without
            # a policy group is a normal state and must be a no-op, not an error.
            team(BARE_TEAM, None),
            member(TEAM, IN_POLICY),
            member(TEAM, OUT_OF_POLICY),
            member(BARE_TEAM, BARE_TEAM_MEMBER),
            Group(
                id=TEAM_GROUP,
                user_id="u-owner",
                name="PII — team",
                description="",
                # The real flag: `remove_users_from_group` refuses a removal from
                # an ENFORCING group without a reason, and that refusal is one of
                # the things this move has to satisfy.
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

    # Every module that took its own module-scope reference to the context
    # manager has to be patched by name — see the note in
    # `test_policy_membership_authz.py`, where patching one of them sent a check
    # to a real database.
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
    """⚠️ Not a style check.

    `member_removed` with a reason that only restates the event reads, to someone
    who was not here, as protection having been taken away from a person. What
    happened is that the team stopped covering them.
    """
    assert "no longer a member of the team" in REASON_LEFT_TEAM
    assert "team" in REASON_LEFT_TEAM.lower()
    # It has to say more than the event type already says on its own.
    assert len(REASON_LEFT_TEAM.split()) > 6


# ---------------------------------------------------------------------------
# The two no-ops — measured by COUNTING rows, not by reading the outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_someone_outside_the_policy_is_not_acted_on(env):
    assert await remove_from_team_policy_group(TEAM, OUT_OF_POLICY) is False


@pytest.mark.asyncio
async def test_someone_outside_the_policy_leaves_no_audit_row(env):
    """⚠️ The test that catches a regression here.

    Deliberately separate from the one above: an implementation that writes the
    row and then removes nobody returns `False` all the same, and the outcome
    assertion cannot tell the two apart.
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
    """⚠️ Written because a mutation SURVIVED: deleting the `group_id` check
    broke nothing.

    It is shadowed. With no group id the membership lookup asks for the members
    of `None`, gets an empty list back, and the second guard returns `False` for
    it — so the two guards produce the same ANSWER and only differ in what they
    do to get there. That makes the first one look removable while it is not: it
    is the difference between "this team has no policy" as a fact and as an
    accident of what a query happens to return for a NULL id.

    Measured at the call, because the outcome cannot tell them apart.
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
    """The record blocks the change, for the system actor too.

    ⚠️ The direction matters: this test passes both for "audit first" and for an
    implementation that mutates first and then rolls back. It is
    `test_a_failed_audit_write_happens_before_the_removal` that separates them.
    """
    with patch.object(
        PiiPolicyAudits, "insert_event", side_effect=RuntimeError("audit is down")
    ):
        with pytest.raises(RuntimeError):
            await remove_from_team_policy_group(TEAM, IN_POLICY)

    assert await _group_members(env) == {IN_POLICY}


@pytest.mark.asyncio
async def test_a_failed_audit_write_happens_before_the_removal(env):
    """Records the order at the two writers, rather than inferring it.

    An implementation that removes the membership and then writes the row would
    reach `remove_users_from_group` first; this asserts it is never reached at
    all once the audit write fails.
    """
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
# Coming back is a separate move, and this one does not make it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejoining_the_team_does_not_put_them_back_in_the_policy(env):
    """O-C6: the reverse move is its own ticket, deliberately not done here.

    Pinned rather than left unstated so that the day it IS built, it is built on
    purpose and not discovered as a surprise by whoever reads the audit trail.
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
    """⚠️ The migrations keep their own copies BECAUSE they are history.

    An applied revision's source cannot be edited, so the duplication is not
    removable — only checkable. Two spellings of "system" would split the audit
    trail into two actors that look like different things and are not.
    """
    assert _module_constant(migration, name) == expected


def test_new_writers_do_not_spell_the_system_actor_themselves():
    """The third literal copy is where drift starts, so there is not one.

    Scoped to code written from here on: migrations are excluded above, with
    their agreement asserted instead.
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
    """Without this, the move is code nothing reaches.

    Structural because the route needs Stripe, billing config and a request to
    run at all — and a test that mounted all three would be proving those work,
    not that this one line is there.
    """
    called = {
        node.func.id
        for node in ast.walk(_remove_team_member_ast())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "remove_from_team_policy_group" in called


def test_the_route_still_takes_no_db_parameter():
    """Pinned by the plan: the ordering decision did not widen the signature.

    A `db` here would make the route a participant in this feature's session
    handling, which is the thing every other caller was kept out of.
    """
    node = _remove_team_member_ast()
    names = [a.arg for a in node.args.args + node.args.kwonlyargs]
    assert "db" not in names, names
