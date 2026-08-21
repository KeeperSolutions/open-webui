"""G-B3 — membership in a group that enforces PII masking.

The rule: **adding is always allowed; removing needs a reason.**

Asymmetric on purpose. Adding someone strengthens protection and asks nothing;
removing takes protection away, so it has to say why — the same rule the group
route already applies to turning the policy off.

⚠️ The criterion is `group_enforces_pii_masking`, NOT "is this a team group".
That is what makes this fix close the LIVE bug: an LDAP login removes the user
from every group the directory does not list, and the global policy group is not
something LDAP knows about. A team-scoped guard would return "not a team group"
for the global one and let the removal through — passing every other test in this
file while the bug stayed open. `test_ldap_sync_*` is the test that catches it.

The route (`routers/groups.py:378`) already refuses a reasonless removal. These
tests are about the MODEL, because OAuth, SCIM and LDAP never pass through that
route — they call these methods directly.
"""

import sys
import time
import uuid
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from open_webui.models.groups import Group, GroupMember, GroupTable


ENFORCING = {"chat": {"pii_masking_enforced": True}}
PLAIN = {"read": {"models": True}}

POLICY_GROUP = "g-policy"
OTHER_GROUP = "g-other"
ALICE, BOB = "u-alice", "u-bob"


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Group.__table__.create, checkfirst=True)
        await conn.run_sync(GroupMember.__table__.create, checkfirst=True)

    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())

    def group(gid, name, permissions):
        return Group(
            id=gid,
            user_id="admin",
            name=name,
            description="",
            data={},
            meta=None,
            permissions=permissions,
            created_at=now,
            updated_at=now,
        )

    def member(gid, uid):
        return GroupMember(
            id=str(uuid.uuid4()), group_id=gid, user_id=uid, created_at=now, updated_at=now
        )

    session.add_all(
        [
            group(POLICY_GROUP, "PII Masking Policy", ENFORCING),
            group(OTHER_GROUP, "Marketing", PLAIN),
            member(POLICY_GROUP, ALICE),
            member(POLICY_GROUP, BOB),
            member(OTHER_GROUP, ALICE),
        ]
    )
    await session.commit()
    yield session
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def groups(db_session):
    """`GroupTable` bound to the in-memory session.

    ⚠️ The patch is required, not cosmetic: `DATABASE_ENABLE_SESSION_SHARING` is
    off by default, so `get_async_db_context` IGNORES a session passed as an
    argument and opens a real one against the developer's own database. Measured,
    not assumed — the same trap documented in `test_user_locate.py`.
    """

    @asynccontextmanager
    async def _ctx(db=None):
        yield db_session

    with patch("open_webui.models.groups.get_async_db_context", _ctx):
        yield GroupTable(), db_session


async def _member_ids(session, group_id):
    from sqlalchemy import select

    result = await session.execute(
        select(GroupMember.user_id).filter(GroupMember.group_id == group_id)
    )
    return {uid for (uid,) in result.all()}


# ---------------------------------------------------------------------------
# remove_users_from_group — the admin/OAuth path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_removal_without_reason_is_refused(groups):
    table, session = groups
    assert await table.remove_users_from_group(POLICY_GROUP, [ALICE]) is None
    assert await _member_ids(session, POLICY_GROUP) == {ALICE, BOB}


@pytest.mark.asyncio
async def test_removal_with_reason_goes_through(groups):
    """The whole point of the revised criterion: removal is possible, not forbidden."""
    table, session = groups
    result = await table.remove_users_from_group(
        POLICY_GROUP, [ALICE], reason="Left the company"
    )
    assert result is not None
    assert await _member_ids(session, POLICY_GROUP) == {BOB}


@pytest.mark.asyncio
async def test_whitespace_is_not_a_reason(groups):
    """Same `strip` rule as `routers/groups.py:378` and `pii_policy_audit.py:138`."""
    table, session = groups
    assert await table.remove_users_from_group(POLICY_GROUP, [ALICE], reason="   \n\t ") is None
    assert await _member_ids(session, POLICY_GROUP) == {ALICE, BOB}


@pytest.mark.asyncio
async def test_removing_a_non_member_is_not_a_removal(groups):
    """A no-op needs no justification.

    ⚠️ Caught by the EXISTING suite, not by this file: the first version of the
    guard refused any reasonless call against an enforcing group, which turned
    "remove someone who was never in the group" into a 400. The route already drew
    this distinction before writing an audit row; the model has to draw the same
    one, or the two disagree about what counts as a removal.
    """
    table, session = groups
    assert await table.remove_users_from_group(POLICY_GROUP, ["u-nobody"]) is not None
    assert await _member_ids(session, POLICY_GROUP) == {ALICE, BOB}


@pytest.mark.asyncio
async def test_removal_from_a_non_enforcing_group_needs_no_reason(groups):
    table, session = groups
    assert await table.remove_users_from_group(OTHER_GROUP, [ALICE]) is not None
    assert await _member_ids(session, OTHER_GROUP) == set()


@pytest.mark.asyncio
async def test_adding_never_needs_a_reason(groups):
    """Adding strengthens protection, so it asks nothing."""
    table, session = groups
    assert await table.add_users_to_group(POLICY_GROUP, ["u-carol"]) is not None
    assert await _member_ids(session, POLICY_GROUP) == {ALICE, BOB, "u-carol"}


# ---------------------------------------------------------------------------
# set_group_user_ids_by_id — the SCIM path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_members_dropping_someone_is_refused(groups):
    table, session = groups
    assert await table.set_group_user_ids_by_id(POLICY_GROUP, [ALICE]) is False
    assert await _member_ids(session, POLICY_GROUP) == {ALICE, BOB}


@pytest.mark.asyncio
async def test_set_members_that_only_adds_goes_through(groups):
    """SCIM must keep being able to ADD to a policy group — O-1 is asymmetric."""
    table, session = groups
    assert await table.set_group_user_ids_by_id(POLICY_GROUP, [ALICE, BOB, "u-carol"]) is True
    assert await _member_ids(session, POLICY_GROUP) == {ALICE, BOB, "u-carol"}


@pytest.mark.asyncio
async def test_set_members_dropping_someone_with_a_reason_goes_through(groups):
    table, session = groups
    assert await table.set_group_user_ids_by_id(POLICY_GROUP, [ALICE], reason="Offboarded") is True
    assert await _member_ids(session, POLICY_GROUP) == {ALICE}


# ---------------------------------------------------------------------------
# sync_groups_by_group_names — the LDAP path, and the live bug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ldap_sync_keeps_membership_of_an_enforcing_group(groups):
    """⚠️ THE regression this gate exists for.

    LDAP claims list only "Marketing". Before the guard, signing in removed the
    user from the policy group as well — silently, with no audit row and nobody
    deciding it.
    """
    table, session = groups
    assert await table.sync_groups_by_group_names(ALICE, ["Marketing"]) is True
    assert ALICE in await _member_ids(session, POLICY_GROUP)


@pytest.mark.asyncio
async def test_ldap_sync_still_removes_from_ordinary_groups(groups):
    """The guard must not turn into "directory sync stops working"."""
    table, session = groups
    assert await table.sync_groups_by_group_names(ALICE, ["PII Masking Policy"]) is True
    assert ALICE not in await _member_ids(session, OTHER_GROUP)


@pytest.mark.asyncio
async def test_ldap_sync_still_adds(groups):
    table, session = groups
    assert await table.sync_groups_by_group_names(BOB, ["PII Masking Policy", "Marketing"]) is True
    assert BOB in await _member_ids(session, OTHER_GROUP)


# ---------------------------------------------------------------------------
# The fourth deletion site, which must stay unguarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_user_still_clears_their_policy_membership(groups):
    """`remove_user_from_all_groups` is exempt, and the exemption is structural.

    It is reached only from `Users.delete_user_by_id`: the account is going away,
    so leaving the row behind would orphan it. Guarding this method would break
    user deletion outright.
    """
    table, session = groups
    assert await table.remove_user_from_all_groups(ALICE) is True
    assert ALICE not in await _member_ids(session, POLICY_GROUP)


def test_remove_user_from_all_groups_has_exactly_one_caller():
    """⚠️ The exemption above is safe only while nothing else can reach it.

    Deliberately not a `force=True` flag: a flag is a bypass waiting for the first
    caller the guard inconveniences. This test is the mechanism that keeps the
    exemption honest — it fails the moment a second caller appears, and whoever
    adds it has to justify it here.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    callers = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name == "groups.py" and path.parent.name == "models":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(r"remove_user_from_all_groups\s*\(", line):
                callers.append(f"{path.relative_to(root)}:{i}")

    assert callers == ["models/users.py:723"], callers
