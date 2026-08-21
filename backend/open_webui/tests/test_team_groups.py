"""G-B1 — `team_group_kind`, and the column it reads.

The property under test is that the classification comes from ONE place and from
ONE fact: the back-reference `teams.group_id`. Every other candidate — the
masking flag, the name prefix — is something a group can carry without belonging
to a team, and each has its own test here proving it is not consulted.

⚠️ `test_group_id_is_read_in_exactly_one_module` is the structural half. It is
not proved by deleting it; it is proved by ADDING a second reader and watching it
fail — see the reverse check recorded in the gate report.
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
from open_webui.utils.team_groups import team_group_kind


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
    """`team_group_kind` bound to the in-memory session.

    ⚠️ Required, not cosmetic: `DATABASE_ENABLE_SESSION_SHARING` is off by
    default, so a session passed as an argument is IGNORED and a real one is
    opened against the developer's own database.
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
    """⚠️ Enforcing AND named like a team group — and still `None`.

    This is the test that dies if the classification is ever taken from the
    masking flag or the name instead of the back-reference. Both are true of this
    group; only the back-reference is absent.
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
    """`teams.group_id` can outlive the group: SQLite does not enforce the FK.

    Measured: `PRAGMA foreign_keys` is 0 on the live database, so deleting a group
    leaves the reference pointing at nothing. The classifier answers from the
    reference, so it still says `team_pii` — and must not raise on the way there.
    """
    from sqlalchemy import delete

    await db_session.execute(delete(Group).filter_by(id=TEAM_GROUP))
    await db_session.commit()
    assert await team_group_kind(TEAM_GROUP) == "team_pii"


@pytest.mark.asyncio
async def test_two_teams_may_both_have_no_group(bound, db_session):
    """A UNIQUE index does not constrain NULL — many teams may await their group."""
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
    """The UNIQUE index is what makes "the team's own group" a single answer."""
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
    """Reserved now so the second kind is added HERE, not as a new `if` elsewhere."""
    import typing

    from open_webui.utils.team_groups import TeamGroupKind

    literal = next(
        arg for arg in typing.get_args(TeamGroupKind) if typing.get_origin(arg) is typing.Literal
    )
    assert set(typing.get_args(literal)) == {"team_pii", "team"}


def test_group_id_is_read_in_exactly_one_module():
    """The mechanism behind "one place", not a comment asking for it.

    ⚠️ Proved by ADDING a second reader, never by deleting this test. A second
    `if` over `teams.group_id` anywhere outside `migrations/` makes this fail, and
    whoever wrote it has to come here and say why.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    readers = set()

    # ⚠️ Parsed, not grepped, and both refinements were forced by a false
    # positive:
    #   * a bare `group_id` also names `group_member.group_id` and
    #     `stripe_billing.group_id` — that reported four innocent modules
    #   * a qualified TEXT match then reported `models/groups.py`, because its
    #     docstring MENTIONS `teams.group_id` while reading nothing
    # An attribute access is the only form that is actually a read.
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
