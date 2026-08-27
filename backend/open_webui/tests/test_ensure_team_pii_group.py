"""G-B4 — the team's PII group, created lazily and exactly once.

⚠️ Lazy creation is a concession to N-6, not a design. `create_team` cannot make
the team and the group atomically, so the group is created on first use instead.
The cost of that concession is that a READ can write, and the thing that keeps it
affordable is that `ensure_team_pii_group` reads first: once the group exists it
issues one SELECT and no write at all.

The dashboard calls three routes per screen and all three resolve the same scope,
so "one write, then none" is not a nicety — a create-then-check shape would
attempt three writes per page load, for every viewer, including an admin looking
at someone else's team. `test_three_calls_write_once` measures that with a
statement counter rather than trusting the shape of the code.
"""

import ast
import inspect
import pathlib
import sys
import time
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from open_webui.models.billing import Team
from open_webui.models.groups import Group
from open_webui.utils.team_groups import (
    TEAM_PII_GROUP_PERMISSIONS,
    ensure_team_pii_group,
    rename_team_pii_group,
    team_pii_group_name,
)


class WriteCounter:
    """Counts the INSERT/UPDATE/DELETE statements the engine actually issues.

    ⚠️ Counted at the cursor, not by patching a model method. A shape that calls
    `insert_new_group` and lets it fail on the unique index would look idempotent
    from above while writing every time.
    """

    def __init__(self):
        self.writes = 0
        self.reads = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        head = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if head in {"INSERT", "UPDATE", "DELETE"}:
            self.writes += 1
        elif head == "SELECT":
            self.reads += 1


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Team.__table__.create, checkfirst=True)
        await conn.run_sync(Group.__table__.create, checkfirst=True)

    counter = WriteCounter()
    event.listen(engine.sync_engine, "before_cursor_execute", counter)

    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())
    session.add_all(
        [
            Team(
                id="team-abcdef01-2345",
                name="Acme",
                owner_user_id="owner",
                seat_limit=10,
                monthly_credits=0,
                group_id=None,
                created_at=now,
                updated_at=now,
            ),
            Team(
                id="team-99999999-0000",
                name="Acme",  # same name on purpose — see the discriminator test
                owner_user_id="owner2",
                seat_limit=10,
                monthly_credits=0,
                group_id=None,
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    await session.commit()

    @asynccontextmanager
    async def _ctx(db=None):
        yield session

    with patch("open_webui.internal.db.get_async_db_context", _ctx), patch(
        "open_webui.models.groups.get_async_db_context", _ctx
    ), patch("open_webui.models.billing.get_async_db_context", _ctx):
        counter.writes = 0
        yield session, counter

    event.remove(engine.sync_engine, "before_cursor_execute", counter)
    await session.close()
    await engine.dispose()


TEAM = "team-abcdef01-2345"
TWIN = "team-99999999-0000"


async def _group(session, group_id):
    result = await session.execute(select(Group).filter_by(id=group_id))
    return result.scalars().first()


@pytest.mark.asyncio
async def test_creates_the_group_and_links_it(env):
    session, _ = env
    gid = await ensure_team_pii_group(TEAM)
    assert gid
    result = await session.execute(select(Team.group_id).filter_by(id=TEAM))
    assert result.scalars().first() == gid


@pytest.mark.asyncio
async def test_permissions_carry_exactly_one_key(env):
    session, _ = env
    gid = await ensure_team_pii_group(TEAM)
    assert (await _group(session, gid)).permissions == TEAM_PII_GROUP_PERMISSIONS
    assert list(TEAM_PII_GROUP_PERMISSIONS) == ["chat"]


@pytest.mark.asyncio
async def test_the_loser_of_a_race_deletes_its_group_and_returns_the_winner(env):
    """⚠️ The `UNIQUE` index does NOT decide this race, and an earlier shape
    believed it did.

    `uq_teams_group_id` is unique ACROSS rows. Two callers racing here write the
    SAME team row with DIFFERENT group ids, which violates nothing: the second
    `UPDATE` simply overwrites the first, no `IntegrityError` is ever raised, and
    the loser's group survives as an orphan.

    An orphan is not cosmetic. It carries the masking permission and nothing
    else, and nothing points at it — so `team_group_kind` calls it an ordinary
    group and the administrator's `Enforce` list offers it as a destination,
    reopening the door that list was narrowed to close.

    The competitor here commits between this call's read and its write, which is
    exactly the window three concurrent dashboard routes open on a first load.
    """
    session, _ = env
    from open_webui.models.groups import Groups

    now = int(time.time())
    real_insert = Groups.insert_new_group

    async def insert_then_lose(*args, **kwargs):
        group = await real_insert(*args, **kwargs)
        session.add(
            Group(
                id="winner",
                user_id="",
                name="winner",
                description="",
                data={},
                meta=None,
                permissions=TEAM_PII_GROUP_PERMISSIONS,
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(update(Team).where(Team.id == TEAM).values(group_id="winner"))
        await session.commit()
        return group

    with patch.object(Groups, "insert_new_group", insert_then_lose):
        gid = await ensure_team_pii_group(TEAM)

    assert gid == "winner"
    # ⚠️ The load-bearing assertion. Returning the winner's id was already true
    # of the shape this replaced; what was NOT true is that the loser's group
    # stops existing.
    remaining = (await session.execute(select(Group.id))).scalars().all()
    assert remaining == ["winner"]


@pytest.mark.asyncio
async def test_second_call_returns_the_same_group(env):
    session, _ = env
    first = await ensure_team_pii_group(TEAM)
    second = await ensure_team_pii_group(TEAM)
    assert first == second
    result = await session.execute(select(Group.id))
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_three_calls_write_once(env):
    """⚠️ The measurement the gate exists for: one write, then none.

    Three calls model one dashboard load, whose three routes all resolve the same
    scope. Counted at the cursor — see `WriteCounter`.
    """
    _, counter = env

    await ensure_team_pii_group(TEAM)
    after_first = counter.writes
    assert after_first > 0, "the first call must actually create something"

    await ensure_team_pii_group(TEAM)
    await ensure_team_pii_group(TEAM)

    assert counter.writes == after_first, (
        f"calls 2 and 3 wrote {counter.writes - after_first} statement(s); "
        "they must read only"
    )


@pytest.mark.asyncio
async def test_later_calls_still_read(env):
    """The complement of the above: "no writes" must not mean "does nothing"."""
    _, counter = env
    await ensure_team_pii_group(TEAM)
    reads_before = counter.reads
    await ensure_team_pii_group(TEAM)
    assert counter.reads > reads_before


@pytest.mark.asyncio
async def test_two_teams_with_the_same_name_get_different_group_names(env):
    """`"My Team"` is auto-generated by the Stripe portal path, so this collides."""
    session, _ = env
    a = await ensure_team_pii_group(TEAM)
    b = await ensure_team_pii_group(TWIN)
    assert a != b
    assert (await _group(session, a)).name != (await _group(session, b)).name


@pytest.mark.asyncio
async def test_the_name_carries_the_team_name_and_a_discriminator(env):
    session, _ = env
    gid = await ensure_team_pii_group(TEAM)
    name = (await _group(session, gid)).name
    assert "Acme" in name
    assert TEAM[:8] in name


@pytest.mark.asyncio
async def test_a_missing_team_is_none_not_an_error(env):
    assert await ensure_team_pii_group("no-such-team") is None


@pytest.mark.asyncio
async def test_a_dangling_reference_is_replaced(env):
    """`PRAGMA foreign_keys` is 0, so a deleted group leaves the link dangling."""
    from sqlalchemy import delete

    session, _ = env
    first = await ensure_team_pii_group(TEAM)
    await session.execute(delete(Group).filter_by(id=first))
    await session.commit()

    second = await ensure_team_pii_group(TEAM)
    assert second is not None and second != first
    assert await _group(session, second) is not None


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renaming_the_team_renames_the_group(env):
    session, _ = env
    gid = await ensure_team_pii_group(TEAM)
    await rename_team_pii_group(TEAM, "Acme Holdings")
    assert (await _group(session, gid)).name == team_pii_group_name("Acme Holdings", TEAM)


@pytest.mark.asyncio
async def test_renaming_a_team_without_a_group_is_not_an_error(env):
    """Under path B a team without a group is normal, not broken."""
    assert await rename_team_pii_group(TEAM, "Whatever") is None


# ---------------------------------------------------------------------------
# Where it is called from — one test per call site, so one removal kills one test
# ---------------------------------------------------------------------------


def _calls_within(function_name: str, module_path: str) -> set:
    """Names called inside one function, read from the source tree.

    Structural rather than behavioural because the alternative is standing up
    Stripe, a webhook payload and four tables to prove a single line is present.
    The property is "this call site exists"; an AST is a direct measurement of it.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    tree = ast.parse((root / module_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return {
                sub.func.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
    raise AssertionError(f"{function_name} not found in {module_path}")


def test_create_team_ensures_the_group():
    assert "ensure_team_pii_group" in _calls_within("create_team", "routers/billing.py")


def test_the_stripe_portal_path_ensures_the_group():
    """⚠️ Separate from `create_team`, and not covered by it.

    A team upgraded through the Stripe billing portal never touches
    `create_team` (`routers/billing.py:1921`). Without its own call site such a
    team has no policy group and nothing says so.
    """
    assert "ensure_team_pii_group" in _calls_within("_handle_stripe_event", "routers/billing.py")


def test_the_rename_route_renames_the_group():
    assert "rename_team_pii_group" in _calls_within("update_team_name", "routers/billing.py")


# ---------------------------------------------------------------------------
# Level A must not have changed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_instance_wide_dashboard_creates_nothing():
    """⚠️ G-B4 edits `resolve_dashboard_scope`, which was delivered in level A.

    The unscoped, admin-only path returns before anything about groups happens.
    If `ensure_team_pii_group` ever moves above that early return, an admin
    opening `/admin/pii-dashboard` starts creating groups for teams they were only
    counting — and every other test in this file still passes.
    """
    from open_webui.utils.team_scope import resolve_dashboard_scope

    called = False

    async def _tripwire(team_id, db=None):
        nonlocal called
        called = True
        return "should-not-happen"

    with patch("open_webui.utils.team_groups.ensure_team_pii_group", _tripwire):
        scope = await resolve_dashboard_scope(MagicMock(role="admin"), None)

    assert scope is None
    assert called is False


def test_resolve_dashboard_scope_still_returns_team_identities():
    """The three level-A routers destructure this; the field is additive only."""
    from open_webui.utils.team_scope import TeamIdentities

    assert TeamIdentities._fields == ("ids", "keys", "group_id")
    assert TeamIdentities(frozenset(), frozenset()).group_id is None


@pytest.mark.asyncio
async def test_the_dashboard_survives_a_failed_group_creation():
    """⚠️ Found by running `test_team_scope.py` ALONE, not by the full suite.

    The first version of this gate made `resolve_dashboard_scope` propagate any
    failure from `ensure_team_pii_group` — turning a read path into something that
    depends on a write succeeding. Two level-A tests failed in isolation and
    passed in the full run, because another module happened to have created the
    `teams` table first. The green full suite was hiding it.

    The group is a LABEL: it lets section 4 say "team policy" instead of "outside
    the team". `teamGroupId: null` is already a supported state, so there is a
    correct thing to fall back to and no reason to take the screen down.
    """
    from open_webui.utils.team_scope import TeamIdentities, resolve_dashboard_scope

    async def _explodes(team_id, db=None):
        raise RuntimeError("no such table: teams")

    async def _may_read(user, team_id, db=None):
        return True

    async def _identities(team_id, db=None):
        return TeamIdentities(frozenset({"u1"}), frozenset({"u1@x.com"}))

    with patch("open_webui.utils.team_groups.ensure_team_pii_group", _explodes), patch(
        "open_webui.utils.team_scope._may_read_team_dashboard", _may_read
    ), patch("open_webui.utils.team_scope.resolve_team_identities", _identities):
        scope = await resolve_dashboard_scope(MagicMock(role="user"), "t1")

    assert scope.ids == frozenset({"u1"})
    assert scope.group_id is None
