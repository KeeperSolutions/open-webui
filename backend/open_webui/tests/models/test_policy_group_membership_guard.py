"""Membership in a group that enforces PII masking: adding is always allowed,
removing needs a reason.

Removing someone stops masking them, so it must say why. The guard keys on
`group_enforces_pii_masking`, not on whether the group belongs to a team, so it
also protects non-team policy groups from LDAP sync, which removes the user from
every group the directory does not list. These tests target the model because
OAuth, SCIM and LDAP call it without the group route.
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

    The patch is required: `DATABASE_ENABLE_SESSION_SHARING` is off by default,
    so `get_async_db_context` ignores a passed session and opens one against the
    developer's own database.
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
    """Removal with a reason is allowed, not forbidden."""
    table, session = groups
    result = await table.remove_users_from_group(
        POLICY_GROUP, [ALICE], reason="Left the company"
    )
    assert result is not None
    assert await _member_ids(session, POLICY_GROUP) == {BOB}


@pytest.mark.asyncio
async def test_whitespace_is_not_a_reason(groups):
    """A whitespace-only reason is refused, matching the route and the audit model."""
    table, session = groups
    assert await table.remove_users_from_group(POLICY_GROUP, [ALICE], reason="   \n\t ") is None
    assert await _member_ids(session, POLICY_GROUP) == {ALICE, BOB}


@pytest.mark.asyncio
async def test_removing_a_non_member_is_not_a_removal(groups):
    """Removing a non-member changes nothing, so it needs no reason.

    The model must count removals the same way the route does, or a harmless
    call would be refused.
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
    """SCIM can always add to a policy group; only removals need a reason."""
    table, session = groups
    assert await table.set_group_user_ids_by_id(POLICY_GROUP, [ALICE, BOB, "u-carol"]) is True
    assert await _member_ids(session, POLICY_GROUP) == {ALICE, BOB, "u-carol"}


@pytest.mark.asyncio
async def test_set_members_dropping_someone_with_a_reason_goes_through(groups):
    table, session = groups
    assert await table.set_group_user_ids_by_id(POLICY_GROUP, [ALICE], reason="Offboarded") is True
    assert await _member_ids(session, POLICY_GROUP) == {ALICE}


# ---------------------------------------------------------------------------
# sync_groups_by_group_names — the LDAP path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ldap_sync_keeps_membership_of_an_enforcing_group(groups):
    """An LDAP login that lists only "Marketing" keeps the user in the policy group.

    Otherwise signing in would silently remove them from masking with no audit row.
    """
    table, session = groups
    assert await table.sync_groups_by_group_names(ALICE, ["Marketing"]) is True
    assert ALICE in await _member_ids(session, POLICY_GROUP)


@pytest.mark.asyncio
async def test_ldap_sync_still_removes_from_ordinary_groups(groups):
    """Directory sync still removes the user from non-enforcing groups."""
    table, session = groups
    assert await table.sync_groups_by_group_names(ALICE, ["PII Masking Policy"]) is True
    assert ALICE not in await _member_ids(session, OTHER_GROUP)


@pytest.mark.asyncio
async def test_ldap_sync_still_adds(groups):
    table, session = groups
    assert await table.sync_groups_by_group_names(BOB, ["PII Masking Policy", "Marketing"]) is True
    assert BOB in await _member_ids(session, OTHER_GROUP)


# ---------------------------------------------------------------------------
# User deletion, which must stay unguarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_user_still_clears_their_policy_membership(groups):
    """`remove_user_from_all_groups` is exempt from the guard.

    Its only caller is `Users.delete_user_by_id`; guarding it would break user
    deletion and orphan the membership rows.
    """
    table, session = groups
    assert await table.remove_user_from_all_groups(ALICE) is True
    assert ALICE not in await _member_ids(session, POLICY_GROUP)


def test_remove_user_from_all_groups_has_exactly_one_caller():
    """The unguarded removal is safe only while user deletion is its sole caller.

    This fails when a second caller appears, which must then be justified here.
    A `force=True` flag is avoided because any caller could use it to bypass the guard.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    callers = {}
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name == "groups.py" and path.parent.name == "models":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(r"remove_user_from_all_groups\s*\(", line):
                callers[str(path.relative_to(root))] = i

    # Compare files, not line numbers, so unrelated edits do not break the test.
    # The line number is kept in the failure message to locate the caller.
    assert set(callers) == {"models/users.py"}, callers
