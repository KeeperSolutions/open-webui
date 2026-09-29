"""Tests for `team_group_kind` and the `teams.group_id` column it reads.

The classification must come only from `teams.group_id`. The masking flag and the
name prefix can appear on groups that belong to no team, and tests here show
neither is consulted.
"""

import sys
import time
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.exc import IntegrityError

sys.modules.setdefault("stripe", MagicMock())

from open_webui.models.billing import Team
from open_webui.models.groups import Group
from open_webui.utils.team_groups import team_group_kind, team_owning_group_id


TEAM_GROUP = "g-team-pii"
CUSTOM_GROUP = "g-custom"
GLOBAL_GROUP = "pii-masking-policy"
ENFORCING = {"chat": {"pii_masking_enforced": True}}


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Team.__table__.create, checkfirst=True)
        await conn.run_sync(Group.__table__.create, checkfirst=True)

    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())

    def team(tid, name, group_id):
        return Team(
            id=tid,
            name=name,
            owner_user_id="owner",
            seat_limit=10,
            monthly_credits=0,
            group_id=group_id,
            created_at=now,
            updated_at=now,
        )

    def group(gid, name, permissions):
        return Group(
            id=gid,
            user_id="",
            name=name,
            description="",
            data={},
            meta=None,
            permissions=permissions,
            created_at=now,
            updated_at=now,
        )

    session.add_all(
        [
            team("t1", "Acme", TEAM_GROUP),
            team("t2", "No group yet", None),
            # Named like a team group and enforcing, but nothing points at it.
            group(TEAM_GROUP, "PII — Acme · t1", ENFORCING),
            group(CUSTOM_GROUP, "PII — Marketing · deadbeef", ENFORCING),
            group(GLOBAL_GROUP, "PII Masking Policy", ENFORCING),
        ]
    )
    await session.commit()
    yield session
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def bound(db_session):
    """Route every DB context to the in-memory session.

    With `DATABASE_ENABLE_SESSION_SHARING` off by default, a passed session is
    ignored and the code would open the developer's real database.
    """

    @asynccontextmanager
    async def _ctx(db=None):
        yield db_session

    with patch("open_webui.internal.db.get_async_db_context", _ctx):
        yield db_session


@pytest.mark.asyncio
async def test_the_group_a_team_points_at_is_team_pii(bound):
    assert await team_group_kind(TEAM_GROUP) == "team_pii"


@pytest.mark.asyncio
async def test_a_group_nothing_points_at_is_not_a_team_group(bound):
    """An enforcing group named like a team group is still `None` without a back-reference.

    Fails if the classification is taken from the masking flag or the name.
    """
    assert await team_group_kind(CUSTOM_GROUP) is None


@pytest.mark.asyncio
async def test_the_global_policy_group_is_not_a_team_group(bound):
    """It enforces masking for everyone without a team; it belongs to no team."""
    assert await team_group_kind(GLOBAL_GROUP) is None


@pytest.mark.asyncio
async def test_unknown_group_id_is_none_not_an_error(bound):
    assert await team_group_kind("g-does-not-exist") is None


@pytest.mark.asyncio
async def test_empty_group_id_is_none_not_an_error(bound):
    assert await team_group_kind("") is None


@pytest.mark.asyncio
async def test_a_dangling_back_reference_does_not_crash(bound, db_session):
    """A reference to a deleted group still classifies as `team_pii` without raising.

    SQLite runs with `PRAGMA foreign_keys` off, so `teams.group_id` can dangle.
    """
    from sqlalchemy import delete

    await db_session.execute(delete(Group).filter_by(id=TEAM_GROUP))
    await db_session.commit()
    assert await team_group_kind(TEAM_GROUP) == "team_pii"


@pytest.mark.asyncio
async def test_two_teams_may_both_have_no_group(bound, db_session):
    """The unique index allows many teams with no group, since NULLs are not constrained."""
    now = int(time.time())
    db_session.add(
        Team(
            id="t3",
            name="Also no group",
            owner_user_id="owner",
            seat_limit=5,
            monthly_credits=0,
            group_id=None,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()  # must not raise


@pytest.mark.asyncio
async def test_two_teams_cannot_share_a_group(bound, db_session):
    """The unique index keeps the answer to "which team owns this group" to one team."""
    now = int(time.time())
    db_session.add(
        Team(
            id="t4",
            name="Thief",
            owner_user_id="owner",
            seat_limit=5,
            monthly_credits=0,
            group_id=TEAM_GROUP,
            created_at=now,
            updated_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


def test_the_team_branch_exists_even_though_nothing_returns_it():
    """`TeamGroupKind` already includes `'team'`, so a new kind is added to the classifier."""
    import typing

    from open_webui.utils.team_groups import TeamGroupKind

    literal = next(
        arg for arg in typing.get_args(TeamGroupKind) if typing.get_origin(arg) is typing.Literal
    )
    assert set(typing.get_args(literal)) == {"team_pii", "team"}


def test_group_id_is_read_in_exactly_one_module():
    """`Team.group_id` is accessed only in `utils/team_groups.py`, outside migrations and tests.

    A second reader anywhere else fails this test.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    readers = set()

    # Matches `Team.group_id` attribute access in the AST. A text search would
    # also hit other tables' `group_id` columns and docstrings that mention it.
    for path in root.rglob("*.py"):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "group_id"
                and isinstance(node.value, ast.Name)
                and node.value.id == "Team"
            ):
                readers.add(str(path.relative_to(root)))

    assert readers == {"utils/team_groups.py"}, sorted(readers)


# ---------------------------------------------------------------------------
# The owner lookup underneath the classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_owner_lookup_returns_the_team(bound):
    """The lookup returns the owning team's id, which the guard in `utils/team_scope.py` needs."""
    assert await team_owning_group_id(TEAM_GROUP) == "t1"


@pytest.mark.asyncio
async def test_the_owner_lookup_is_none_when_no_team_claims_the_group(bound):
    """Enforcing and named like a team group; still owned by nobody."""
    assert await team_owning_group_id(CUSTOM_GROUP) is None
    assert await team_owning_group_id(GLOBAL_GROUP) is None
    assert await team_owning_group_id("g-does-not-exist") is None


@pytest.mark.asyncio
async def test_an_empty_group_id_costs_no_query():
    """An empty or `None` id returns `None` without opening a session.

    The session context raises if entered.
    """

    @asynccontextmanager
    async def _explodes(db=None):
        raise AssertionError("team_owning_group_id opened a session for an empty id")
        yield  # pragma: no cover — unreachable, keeps this a generator

    with patch("open_webui.internal.db.get_async_db_context", _explodes):
        assert await team_owning_group_id("") is None
        assert await team_owning_group_id(None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group_id,expected",
    [(TEAM_GROUP, "team_pii"), (CUSTOM_GROUP, None), (GLOBAL_GROUP, None), ("", None)],
)
async def test_the_classification_is_unchanged_by_the_extraction(bound, group_id, expected):
    """`team_group_kind` gives the expected kind for team, custom, global and empty ids."""
    assert await team_group_kind(group_id) == expected


@pytest.mark.asyncio
async def test_team_group_kind_asks_the_owner_lookup(bound):
    """`team_group_kind` follows a patched `team_owning_group_id` in both directions.

    The structural test cannot catch a second query in the same module. Checking
    both directions rules out a classifier that returns `None` for everything.
    """

    async def _claims_everything(group_id, db=None):
        return "t-somebody"

    async def _claims_nothing(group_id, db=None):
        return None

    with patch("open_webui.utils.team_groups.team_owning_group_id", _claims_everything):
        assert await team_group_kind(CUSTOM_GROUP) == "team_pii"

    with patch("open_webui.utils.team_groups.team_owning_group_id", _claims_nothing):
        assert await team_group_kind(TEAM_GROUP) is None
