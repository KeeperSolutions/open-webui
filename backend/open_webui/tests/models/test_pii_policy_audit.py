"""Tests for the PII policy audit log.

Two halves:
  * the writer's invariants, which the DDL cannot express
  * the route that emits `policy_*` events

The route half exercises the real `update_group_by_id` handler against an
in-memory SQLite database, rather than mocking `Groups`. The property under test
is "the policy in the database is unchanged when the audit write is refused" —
mocking the group table away would leave exactly that unverified.
"""

import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from fastapi import HTTPException

from open_webui.models.groups import Group, GroupMember, GroupPolicyUpdateForm
from open_webui.models.pii_policy_audit import (
    EVENT_MEMBER_ADDED,
    EVENT_MEMBER_REMOVED,
    EVENT_POLICY_DISABLED,
    EVENT_POLICY_ENABLED,
    PiiPolicyAudit,
    PiiPolicyAuditTable,
)


ACTOR = dict(actor_user_id="admin-1", actor_email="admin@example.com")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Only the two tables under test — unrelated tables would drag in FKs.
        await conn.run_sync(PiiPolicyAudit.__table__.create, checkfirst=True)
        await conn.run_sync(Group.__table__.create, checkfirst=True)
        # The route's success path counts members before responding.
        await conn.run_sync(GroupMember.__table__.create, checkfirst=True)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(GroupMember.__table__.drop)
        await conn.run_sync(Group.__table__.drop)
        await conn.run_sync(PiiPolicyAudit.__table__.drop)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    Session = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    session = Session()
    yield session
    await session.rollback()
    await session.execute(PiiPolicyAudit.__table__.delete())
    await session.execute(GroupMember.__table__.delete())
    await session.execute(Group.__table__.delete())
    await session.commit()
    await session.close()


@pytest_asyncio.fixture
async def audits(db_session):
    """PiiPolicyAuditTable bound to the in-memory session."""

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        yield db_session

    with patch("open_webui.models.pii_policy_audit.get_async_db_context", _get_async_db_context):
        yield PiiPolicyAuditTable()


@pytest_asyncio.fixture
async def groups_bound(db_session):
    """`Groups` (the real GroupTable) bound to the in-memory session."""
    from open_webui.models import groups as groups_module

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        yield db_session

    with patch.object(groups_module, "get_async_db_context", _get_async_db_context):
        yield groups_module.Groups


async def _make_group(db_session, group_id="g1", enforced=False):
    now = int(time.time())
    db_session.add(
        Group(
            id=group_id,
            user_id="admin-1",
            name="Policy",
            description="",
            permissions={"chat": {"pii_masking_enforced": enforced, "temporary": True}},
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()


async def _stored_enforced(db_session, group_id="g1"):
    result = await db_session.execute(select(Group).filter_by(id=group_id))
    group = result.scalars().first()
    return (group.permissions or {}).get("chat", {}).get("pii_masking_enforced")


def _form(enforced, reason=None):
    return GroupPolicyUpdateForm(
        name="Policy",
        description="",
        permissions={"chat": {"pii_masking_enforced": enforced, "temporary": True}},
        reason=reason,
    )


def _make_request():
    """Minimal request stub for the group routes — they read
    request.app.state.config.USER_PERMISSIONS (policy resolution) and
    request.app.state.instance_id (event publishing)."""
    request = MagicMock()
    request.app.state.config.USER_PERMISSIONS = {}
    request.app.state.instance_id = "test-instance"
    request.state = SimpleNamespace()
    return request


async def _call_route(group_id, form, db_session, audits, groups_bound):
    from open_webui.routers import groups as groups_router

    with patch.object(groups_router, "Groups", groups_bound), patch.object(groups_router, "PiiPolicyAudits", audits):
        return await groups_router.update_group_by_id(
            request=_make_request(),
            id=group_id,
            form_data=form,
            user=MagicMock(id="admin-1", email="admin@example.com"),
            db=db_session,
        )


# ---------------------------------------------------------------------------
# The invariant the DDL cannot express, in BOTH directions
# ---------------------------------------------------------------------------


class TestUserIdInvariant:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", [EVENT_POLICY_ENABLED, EVENT_POLICY_DISABLED])
    async def test_policy_events_must_not_carry_user_id(self, audits, event_type):
        with pytest.raises(ValueError, match="must not carry user_id"):
            await audits.insert_event(
                event_type=event_type,
                group_id="g1",
                user_id="u1",
                reason="because",
                **ACTOR,
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", [EVENT_MEMBER_ADDED, EVENT_MEMBER_REMOVED])
    async def test_member_events_require_user_id(self, audits, event_type):
        with pytest.raises(ValueError, match="requires user_id"):
            await audits.insert_event(event_type=event_type, group_id="g1", reason="because", **ACTOR)

    @pytest.mark.asyncio
    async def test_policy_events_store_null_user_id(self, audits):
        row = await audits.insert_event(event_type=EVENT_POLICY_ENABLED, group_id="g1", **ACTOR)
        assert row.user_id is None

    @pytest.mark.asyncio
    async def test_member_events_store_the_user_id(self, audits):
        row = await audits.insert_event(event_type=EVENT_MEMBER_ADDED, group_id="g1", user_id="u1", **ACTOR)
        assert row.user_id == "u1"

    @pytest.mark.asyncio
    async def test_unknown_event_type_is_refused(self, audits):
        with pytest.raises(ValueError, match="unknown pii policy audit event_type"):
            await audits.insert_event(event_type="policy_maybe", group_id="g1", **ACTOR)


class TestReasonRule:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "event_type,user_id",
        [(EVENT_POLICY_DISABLED, None), (EVENT_MEMBER_REMOVED, "u1")],
    )
    async def test_removals_require_a_reason(self, audits, event_type, user_id):
        with pytest.raises(ValueError, match="requires a reason"):
            await audits.insert_event(event_type=event_type, group_id="g1", user_id=user_id, **ACTOR)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "event_type,user_id",
        [(EVENT_POLICY_DISABLED, None), (EVENT_MEMBER_REMOVED, "u1")],
    )
    async def test_whitespace_is_not_a_reason(self, audits, event_type, user_id):
        with pytest.raises(ValueError, match="requires a reason"):
            await audits.insert_event(event_type=event_type, group_id="g1", user_id=user_id, reason="   ", **ACTOR)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "event_type,user_id",
        [(EVENT_POLICY_ENABLED, None), (EVENT_MEMBER_ADDED, "u1")],
    )
    async def test_additions_do_not_require_a_reason(self, audits, event_type, user_id):
        row = await audits.insert_event(event_type=event_type, group_id="g1", user_id=user_id, **ACTOR)
        assert row.reason is None


class TestActorAndShape:
    @pytest.mark.asyncio
    async def test_actor_email_is_stored_alongside_the_id(self, audits, db_session):
        await audits.insert_event(event_type=EVENT_POLICY_ENABLED, group_id="g1", **ACTOR)
        result = await db_session.execute(select(PiiPolicyAudit))
        row = result.scalars().one()
        assert row.actor_user_id == "admin-1"
        # Denormalised on purpose: the id alone stops answering "who" once the
        # account is deleted.
        assert row.actor_email == "admin@example.com"

    @pytest.mark.asyncio
    async def test_group_id_and_actor_are_mandatory(self, audits):
        with pytest.raises(ValueError, match="group_id is required"):
            await audits.insert_event(event_type=EVENT_POLICY_ENABLED, group_id="", **ACTOR)
        with pytest.raises(ValueError, match="actor_user_id and actor_email are required"):
            await audits.insert_event(
                event_type=EVENT_POLICY_ENABLED,
                group_id="g1",
                actor_user_id="admin-1",
                actor_email="",
            )

    @pytest.mark.asyncio
    async def test_events_read_back_in_chronological_order(self, audits):
        await audits.insert_event(event_type=EVENT_POLICY_ENABLED, group_id="g1", **ACTOR)
        await audits.insert_event(event_type=EVENT_POLICY_DISABLED, group_id="g1", reason="pilot over", **ACTOR)
        await audits.insert_event(event_type=EVENT_POLICY_ENABLED, group_id="g2", **ACTOR)

        chronology = await audits.get_events_by_group_id("g1")
        assert [e.event_type for e in chronology] == [EVENT_POLICY_ENABLED, EVENT_POLICY_DISABLED]


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


class TestRouteEmitsPolicyEvents:
    @pytest.mark.asyncio
    async def test_t19_enabling_records_an_event_without_a_reason(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=False)

        await _call_route("g1", _form(True), db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        row = result.scalars().one()
        assert row.event_type == EVENT_POLICY_ENABLED
        assert row.user_id is None
        assert row.reason is None
        assert row.group_id == "g1"
        assert await _stored_enforced(db_session) is True

    @pytest.mark.asyncio
    async def test_t20_disabling_without_a_reason_is_refused(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=True)

        with pytest.raises(HTTPException) as exc:
            await _call_route("g1", _form(False), db_session, audits, groups_bound)

        assert exc.value.status_code == 400
        # The point of the test is not the status code: the policy must still
        # be on afterwards, and nothing may be in the log.
        assert await _stored_enforced(db_session) is True
        result = await db_session.execute(select(PiiPolicyAudit))
        assert len(result.scalars().all()) == 0

    @pytest.mark.asyncio
    async def test_t20b_disabling_with_a_reason_goes_through(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=True)

        await _call_route("g1", _form(False, reason="pilot finished"), db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        row = result.scalars().one()
        assert row.event_type == EVENT_POLICY_DISABLED
        assert row.reason == "pilot finished"
        assert await _stored_enforced(db_session) is False

    @pytest.mark.asyncio
    async def test_t21_a_failed_audit_write_rejects_the_mutation(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=False)
        broken = MagicMock()
        broken.insert_event = AsyncMock(side_effect=RuntimeError("audit table is gone"))

        with pytest.raises(HTTPException) as exc:
            await _call_route("g1", _form(True), db_session, broken, groups_bound)

        assert exc.value.status_code == 500
        # The whole reason the write is ordered before the mutation.
        assert await _stored_enforced(db_session) is False

    @pytest.mark.asyncio
    async def test_changing_another_permission_records_nothing(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=True)
        form = GroupPolicyUpdateForm(
            name="Policy",
            description="",
            permissions={"chat": {"pii_masking_enforced": True, "temporary": False}},
        )

        await _call_route("g1", form, db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        assert len(result.scalars().all()) == 0
        assert await _stored_enforced(db_session) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [True, False])
    async def test_resaving_the_same_value_records_nothing(self, db_session, audits, groups_bound, value):
        """Idempotency: only transitions are logged.

        A row saying "enabled" for a save that enabled nothing asserts a change
        that did not happen — and since this modal posts the whole permissions
        dict on every save, the alternative would fill the table with rows for
        edits that never touched the policy.
        """
        await _make_group(db_session, enforced=value)

        await _call_route("g1", _form(value, reason="ignored"), db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        assert len(result.scalars().all()) == 0
        assert await _stored_enforced(db_session) is value

    @pytest.mark.asyncio
    async def test_permissions_omitted_records_nothing(self, db_session, audits, groups_bound):
        """`permissions=None` changes nothing — update_group_by_id drops it."""
        await _make_group(db_session, enforced=True)
        form = GroupPolicyUpdateForm(name="Policy", description="", permissions=None)

        await _call_route("g1", form, db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        assert len(result.scalars().all()) == 0
        assert await _stored_enforced(db_session) is True

    @pytest.mark.asyncio
    async def test_reason_never_reaches_the_group_row(self, db_session, audits, groups_bound):
        """`reason` is audit data, not a group column."""
        await _make_group(db_session, enforced=True)

        await _call_route("g1", _form(False, reason="pilot finished"), db_session, audits, groups_bound)

        result = await db_session.execute(select(Group).filter_by(id="g1"))
        group = result.scalars().first()
        assert "reason" not in (group.permissions or {})
        assert not hasattr(group, "reason")


# ---------------------------------------------------------------------------
# Membership of a policy group
# ---------------------------------------------------------------------------


async def _add_member(db_session, group_id="g1", user_id="u1"):
    db_session.add(
        GroupMember(
            id=f"{group_id}:{user_id}",
            group_id=group_id,
            user_id=user_id,
            created_at=int(time.time()),
            updated_at=int(time.time()),
        )
    )
    await db_session.commit()


async def _members(db_session, group_id="g1"):
    result = await db_session.execute(select(GroupMember).filter_by(group_id=group_id))
    return {m.user_id for m in result.scalars().all()}


async def _call_membership(action, group_id, user_ids, db_session, audits, groups_bound, reason=None):
    from open_webui.models.groups import GroupMembershipForm
    from open_webui.routers import groups as groups_router

    handler = groups_router.add_user_to_group if action == "add" else groups_router.remove_users_from_group

    async def _get_valid_user_ids(ids, db=None):
        return ids

    with (
        patch.object(groups_router, "Groups", groups_bound),
        patch.object(groups_router, "PiiPolicyAudits", audits),
        patch.object(groups_router.Users, "get_valid_user_ids", _get_valid_user_ids),
    ):
        return await handler(
            request=_make_request(),
            id=group_id,
            form_data=GroupMembershipForm(user_ids=user_ids, reason=reason),
            user=MagicMock(id="admin-1", email="admin@example.com"),
            db=db_session,
        )


class TestMembershipAudit:
    @pytest.mark.asyncio
    async def test_adding_to_a_policy_group_records_member_added(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=True)

        await _call_membership("add", "g1", ["u1"], db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        row = result.scalars().one()
        assert row.event_type == EVENT_MEMBER_ADDED
        assert row.user_id == "u1"
        assert row.group_id == "g1"
        # Adding puts someone UNDER protection, so no reason is owed.
        assert row.reason is None
        assert await _members(db_session) == {"u1"}

    @pytest.mark.asyncio
    async def test_removing_without_a_reason_is_refused_and_changes_nothing(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=True)
        await _add_member(db_session)

        with pytest.raises(HTTPException) as exc:
            await _call_membership("remove", "g1", ["u1"], db_session, audits, groups_bound)

        assert exc.value.status_code == 400
        # The status code is not the point: the membership must survive.
        assert await _members(db_session) == {"u1"}
        result = await db_session.execute(select(PiiPolicyAudit))
        assert len(result.scalars().all()) == 0

    @pytest.mark.asyncio
    async def test_removing_with_a_reason_goes_through(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=True)
        await _add_member(db_session)

        await _call_membership("remove", "g1", ["u1"], db_session, audits, groups_bound, reason="left the pilot")

        result = await db_session.execute(select(PiiPolicyAudit))
        row = result.scalars().one()
        assert row.event_type == EVENT_MEMBER_REMOVED
        assert row.user_id == "u1"
        assert row.reason == "left the pilot"
        assert await _members(db_session) == set()

    @pytest.mark.asyncio
    async def test_a_failed_audit_write_leaves_the_membership_alone(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=True)
        await _add_member(db_session)
        broken = MagicMock()
        broken.insert_event = AsyncMock(side_effect=RuntimeError("audit table is gone"))

        with pytest.raises(HTTPException) as exc:
            await _call_membership("remove", "g1", ["u1"], db_session, broken, groups_bound, reason="because")

        assert exc.value.status_code == 500
        assert await _members(db_session) == {"u1"}

    @pytest.mark.asyncio
    async def test_an_ordinary_group_is_neither_audited_nor_gated(self, db_session, audits, groups_bound):
        """A group without the policy keeps its previous behaviour exactly.

        This is the guard on the collateral: the reason requirement must not
        leak onto every group membership change in the product.
        """
        await _make_group(db_session, enforced=False)
        await _add_member(db_session)

        await _call_membership("remove", "g1", ["u1"], db_session, audits, groups_bound)

        assert await _members(db_session) == set()
        result = await db_session.execute(select(PiiPolicyAudit))
        assert len(result.scalars().all()) == 0

    @pytest.mark.asyncio
    async def test_adding_an_existing_member_records_nothing(self, db_session, audits, groups_bound):
        """Idempotency, same rule as the policy events: transitions only."""
        await _make_group(db_session, enforced=True)
        await _add_member(db_session)

        await _call_membership("add", "g1", ["u1"], db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        assert len(result.scalars().all()) == 0

    @pytest.mark.asyncio
    async def test_removing_a_non_member_records_nothing_and_needs_no_reason(self, db_session, audits, groups_bound):
        # Nothing is being taken away, so there is nothing to justify — and a row
        # would claim a removal that never happened.
        await _make_group(db_session, enforced=True)

        await _call_membership("remove", "g1", ["u2"], db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        assert len(result.scalars().all()) == 0

    @pytest.mark.asyncio
    async def test_one_row_per_user_when_several_change_at_once(self, db_session, audits, groups_bound):
        await _make_group(db_session, enforced=True)

        await _call_membership("add", "g1", ["u1", "u2"], db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        rows = result.scalars().all()
        assert {r.user_id for r in rows} == {"u1", "u2"}
        assert all(r.event_type == EVENT_MEMBER_ADDED for r in rows)


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------


async def _call_audit(group_id, db_session, groups_bound, role="admin", user_id="admin-1"):
    from open_webui.routers import groups as groups_router

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        yield db_session

    async def _get_users_by_user_ids(ids, db=None):
        return []

    with (
        patch.object(groups_router, "Groups", groups_bound),
        patch("open_webui.models.pii_policy_audit.get_async_db_context", _get_async_db_context),
        patch.object(groups_router.Users, "get_users_by_user_ids", _get_users_by_user_ids),
    ):
        return await groups_router.get_pii_policy_audit_by_group_id(
            id=group_id,
            user=MagicMock(id=user_id, role=role, email="admin@example.com"),
            db=db_session,
        )


async def _seed(db_session, group_id="g1"):
    """Two policy events and two membership events, written out of order."""
    rows = [
        (EVENT_POLICY_ENABLED, None, None, 100),
        (EVENT_MEMBER_ADDED, "u1", None, 200),
        (EVENT_MEMBER_REMOVED, "u1", "left the pilot", 300),
        (EVENT_POLICY_DISABLED, None, "pilot over", 400),
    ]
    for i, (event_type, user_id, reason, ts) in enumerate(rows):
        db_session.add(
            PiiPolicyAudit(
                id=f"{group_id}-{i}",
                event_type=event_type,
                group_id=group_id,
                user_id=user_id,
                actor_user_id="admin-1",
                actor_email="admin@example.com",
                reason=reason,
                event_ts=ts,
            )
        )
    await db_session.commit()


class TestAuditReader:
    @pytest.mark.asyncio
    async def test_returns_only_the_requested_group(self, db_session, groups_bound):
        await _seed(db_session, "g1")
        await _seed(db_session, "g2")

        res = await _call_audit("g1", db_session, groups_bound)

        assert res.total == 4
        assert {e.group_id for e in res.items} == {"g1"}

    @pytest.mark.asyncio
    async def test_orders_newest_first(self, db_session, groups_bound):
        # The panel shows the latest slice, so the newest must survive a cut.
        await _seed(db_session)

        res = await _call_audit("g1", db_session, groups_bound)

        assert [e.event_ts for e in res.items] == [400, 300, 200, 100]

    @pytest.mark.asyncio
    async def test_distinguishes_all_four_event_types(self, db_session, groups_bound):
        await _seed(db_session)

        res = await _call_audit("g1", db_session, groups_bound)

        assert {e.event_type for e in res.items} == {
            EVENT_POLICY_ENABLED,
            EVENT_POLICY_DISABLED,
            EVENT_MEMBER_ADDED,
            EVENT_MEMBER_REMOVED,
        }

    @pytest.mark.asyncio
    async def test_member_events_carry_a_user_and_policy_events_do_not(self, db_session, groups_bound):
        await _seed(db_session)

        res = await _call_audit("g1", db_session, groups_bound)
        by_type = {e.event_type: e for e in res.items}

        assert by_type[EVENT_MEMBER_ADDED].user_id == "u1"
        assert by_type[EVENT_MEMBER_REMOVED].user_id == "u1"
        assert by_type[EVENT_POLICY_ENABLED].user_id is None
        assert by_type[EVENT_POLICY_DISABLED].user_id is None

    @pytest.mark.asyncio
    async def test_reasons_survive_to_the_reader(self, db_session, groups_bound):
        # The one thing an admin was forced to write must reach the screen.
        await _seed(db_session)

        res = await _call_audit("g1", db_session, groups_bound)
        reasons = {e.event_type: e.reason for e in res.items}

        assert reasons[EVENT_POLICY_DISABLED] == "pilot over"
        assert reasons[EVENT_MEMBER_REMOVED] == "left the pilot"
        assert reasons[EVENT_POLICY_ENABLED] is None

    @pytest.mark.asyncio
    async def test_a_group_with_no_events_is_empty_not_an_error(self, db_session, groups_bound):
        res = await _call_audit("g-none", db_session, groups_bound)

        assert res.items == []
        assert res.total == 0

    @pytest.mark.asyncio
    async def test_non_admin_is_refused(self, db_session, groups_bound):
        await _seed(db_session)

        with pytest.raises(HTTPException) as exc:
            await _call_audit("g1", db_session, groups_bound, role="user", user_id="u1")

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_a_truncated_page_still_reports_the_true_total(self, db_session, groups_bound):
        """The limit may cut the list; it may never cut the count.

        Silent truncation is the failure mode this whole dashboard is built
        against — a page showing 200 of 431 while claiming nothing is a lie by
        omission.
        """
        from open_webui.routers import groups as groups_router

        now = 1000
        for i in range(groups_router.PII_AUDIT_PAGE_LIMIT + 5):
            db_session.add(
                PiiPolicyAudit(
                    id=f"many-{i}",
                    event_type=EVENT_POLICY_ENABLED,
                    group_id="g1",
                    actor_user_id="admin-1",
                    actor_email="admin@example.com",
                    event_ts=now + i,
                )
            )
        await db_session.commit()

        res = await _call_audit("g1", db_session, groups_bound)

        assert len(res.items) == groups_router.PII_AUDIT_PAGE_LIMIT
        assert res.total == groups_router.PII_AUDIT_PAGE_LIMIT + 5
        # The newest end is what survived the cut.
        assert res.items[0].event_ts == now + groups_router.PII_AUDIT_PAGE_LIMIT + 4
