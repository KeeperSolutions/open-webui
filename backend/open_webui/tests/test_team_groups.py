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


# ---------------------------------------------------------------------------
# G-C1 — the lookup underneath the classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_owner_lookup_returns_the_team(bound):
    """The guard in `utils/team_scope.py` needs the TEAM, not a classification.

    `team_group_kind` throws the id away — deliberately, because its callers only
    ask "is this a team's group". Authorisation asks "which team", and then
    "whose", so it needs the value the classifier discards.
    """
    assert await team_owning_group_id(TEAM_GROUP) == "t1"


@pytest.mark.asyncio
async def test_the_owner_lookup_is_none_when_no_team_claims_the_group(bound):
    """Enforcing and named like a team group; still owned by nobody."""
    assert await team_owning_group_id(CUSTOM_GROUP) is None
    assert await team_owning_group_id(GLOBAL_GROUP) is None
    assert await team_owning_group_id("g-does-not-exist") is None


@pytest.mark.asyncio
async def test_an_empty_group_id_costs_no_query():
    """⚠️ Not "returns None" — that is the test above. This is "opens no session".

    The authorisation guard calls this before refusing, and a refusal should not
    pay for a round trip to learn that an empty id matches nothing. Proved by
    making the session context explode: if it is ever entered, the test fails
    rather than quietly passing on the right answer for the wrong reason.
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
    """G-C1 is a refactor. Every answer `team_group_kind` gave before, it still gives."""
    assert await team_group_kind(group_id) == expected


@pytest.mark.asyncio
async def test_team_group_kind_asks_the_owner_lookup(bound):
    """⚠️ Both directions, because one direction is not enough.

    The structural test cannot see this: `team_group_kind` and
    `team_owning_group_id` live in the SAME module, so a second query written
    inside the classifier reads `Team.group_id` from a module that is already
    allowed to. Patching the lookup is the only thing that distinguishes "asks"
    from "happens to agree".

    Checking only the None direction would pass for a classifier that had grown
    its own query and returned None for everything.
    """

    async def _claims_everything(group_id, db=None):
        return "t-somebody"

    async def _claims_nothing(group_id, db=None):
        return None

    with patch("open_webui.utils.team_groups.team_owning_group_id", _claims_everything):
        assert await team_group_kind(CUSTOM_GROUP) == "team_pii"

    with patch("open_webui.utils.team_groups.team_owning_group_id", _claims_nothing):
        assert await team_group_kind(TEAM_GROUP) is None
