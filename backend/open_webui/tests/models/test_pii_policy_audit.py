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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from fastapi import HTTPException

from open_webui.models.billing import Team
from open_webui.models.groups import Group, GroupMember, GroupPolicyUpdateForm
from open_webui.models.pii_policy_audit import (
    EVENT_MEMBER_ADDED,
    EVENT_MEMBER_REMOVED,
    EVENT_POLICY_DISABLED,
    EVENT_POLICY_ENABLED,
    PiiPolicyAudit,
    PiiPolicyAuditTable,
    validate_pii_policy_event,
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
        # ⚠️ Required since the route refuses a team group's derived edits before
        # writing anything: `team_group_kind` reads `teams`. Deliberately NOT
        # made tolerant of a missing table — a route that cannot tell whether a
        # group belongs to a team must fail loudly, not guess.
        await conn.run_sync(Team.__table__.create, checkfirst=True)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Team.__table__.drop)
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
    await session.execute(Team.__table__.delete())
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
    """`Groups` (the real GroupTable) bound to the in-memory session.

    ⚠️ BOTH context managers are patched. `models.groups` opens its own, and
    `team_group_kind` reaches for `internal.db`'s. Patching only the first leaves
    the classifier talking to the developer's own database — it answers "not a
    team group" for everything, and every guard that depends on it silently stops
    guarding while the tests still pass. Measured here: the team-group tests
    below failed on exactly that, in the direction that looks like the guard is
    missing rather than the fixture.
    """
    from open_webui.models import groups as groups_module

    @asynccontextmanager
    async def _get_async_db_context(db=None):
        yield db_session

    with patch.object(groups_module, "get_async_db_context", _get_async_db_context), patch(
        "open_webui.internal.db.get_async_db_context", _get_async_db_context
    ):
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


async def _call_route(group_id, form, db_session, audits, groups_bound):
    from open_webui.routers import groups as groups_router

    with patch.object(groups_router, "Groups", groups_bound), patch.object(groups_router, "PiiPolicyAudits", audits):
        return await groups_router.update_group_by_id(
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
            id=group_id,
            form_data=GroupMembershipForm(user_ids=user_ids, reason=reason),
            user=MagicMock(id="admin-1", email="admin@example.com"),
            db=db_session,
        )


async def _make_team_group(db_session, group_id="g-team", team_id="t1", enforced=True):
    """A group a team owns: the group, plus the team whose `group_id` points at it."""
    now = int(time.time())
    db_session.add(
        Group(
            id=group_id,
            user_id="",
            name="PII \u2014 Acme \u00b7 t1",
            description="",
            permissions={"chat": {"pii_masking_enforced": enforced}},
            created_at=now,
            updated_at=now,
        )
    )
    db_session.add(
        Team(
            id=team_id,
            name="Acme",
            owner_user_id="owner",
            seat_limit=10,
            monthly_credits=0,
            group_id=group_id,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()


def _team_form(name="PII \u2014 Acme \u00b7 t1", enforced=True, description="", reason=None):
    return GroupPolicyUpdateForm(
        name=name,
        description=description,
        permissions={"chat": {"pii_masking_enforced": enforced}},
        reason=reason,
    )


class TestRefusalIsNotRecorded:
    """\u26a0\ufe0f A refused edit must leave NOTHING in the audit log.

    The route writes the audit row before the mutation, on purpose: the model
    commits on its own session, so there is no shared transaction to roll back,
    and "no record \u2192 no mutation" only holds in that order (D-6). That ordering
    was chosen when `update_group_by_id` returning None meant one thing \u2014 the
    database failed \u2014 and the already-committed row was accepted as a narrow
    residual.

    The team-group guard gave the same None a second meaning: a guard working
    correctly. From then on EVERY refused edit of a team group wrote a row
    claiming an administrator disabled a policy they never touched. Measured in
    the browser against the running application, not deduced.

    A missing audit row says something is absent. A false one accuses somebody.
    """

    @pytest.mark.asyncio
    async def test_a_refused_policy_change_records_nothing(self, db_session, audits, groups_bound):
        await _make_team_group(db_session)

        with pytest.raises(HTTPException) as exc:
            await _call_route(
                "g-team", _team_form(enforced=False, reason="testing"), db_session, audits, groups_bound
            )

        assert exc.value.status_code == 400
        result = await db_session.execute(select(PiiPolicyAudit))
        assert result.scalars().all() == []
        assert await _stored_enforced(db_session, "g-team") is True

    @pytest.mark.asyncio
    async def test_a_refused_rename_records_nothing_and_changes_nothing(
        self, db_session, audits, groups_bound
    ):
        await _make_team_group(db_session)

        with pytest.raises(HTTPException) as exc:
            await _call_route("g-team", _team_form(name="Hijacked"), db_session, audits, groups_bound)

        assert exc.value.status_code == 400
        result = await db_session.execute(select(Group).filter_by(id="g-team"))
        assert result.scalars().first().name == "PII \u2014 Acme \u00b7 t1"
        result = await db_session.execute(select(PiiPolicyAudit))
        assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_the_refusal_says_the_group_belongs_to_a_team(self, db_session, audits, groups_bound):
        """Not the generic "Error updating group" the model's None produced."""
        await _make_team_group(db_session)

        with pytest.raises(HTTPException) as exc:
            await _call_route("g-team", _team_form(name="Hijacked"), db_session, audits, groups_bound)

        assert "team" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_an_ordinary_group_is_unaffected(self, db_session, audits, groups_bound):
        """The check must not become "no policy group may be edited"."""
        await _make_group(db_session, enforced=False)

        await _call_route("g1", _form(True), db_session, audits, groups_bound)

        result = await db_session.execute(select(PiiPolicyAudit))
        assert result.scalars().one().event_type == EVENT_POLICY_ENABLED
        assert await _stored_enforced(db_session) is True

    @pytest.mark.asyncio
    async def test_restating_a_team_groups_own_values_still_goes_through(
        self, db_session, audits, groups_bound
    ):
        """\u26a0\ufe0f The route refuses a CHANGE, never a restatement.

        Same rule as the model guard, and the same reason: SCIM resends the
        current name on every membership edit and OAuth writes a group's own
        permissions straight back to it. A route check that refused any non-None
        `name` would pass every other test here and break directory sync.
        """
        await _make_team_group(db_session)

        await _call_route("g-team", _team_form(description="New blurb"), db_session, audits, groups_bound)

        result = await db_session.execute(select(Group).filter_by(id="g-team"))
        assert result.scalars().first().description == "New blurb"

    @pytest.mark.asyncio
    async def test_the_model_guard_is_still_the_backstop(self, db_session, groups_bound):
        """\u26a0\ufe0f The route is NOT the protection, and must not become it.

        SCIM and OAuth reach `Groups.update_group_by_id` without passing through
        this handler. Moving the refusal into the route would leave them
        unguarded while every route test above still passed \u2014 so the model keeps
        its own guard, and this is the test that notices if it is removed.
        """
        await _make_team_group(db_session)

        assert await groups_bound.update_group_by_id("g-team", _team_form(name="Hijacked")) is None

        result = await db_session.execute(select(Group).filter_by(id="g-team"))
        assert result.scalars().first().name == "PII \u2014 Acme \u00b7 t1"


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


# ---------------------------------------------------------------------------
# G-B5 — the validator, reachable without a database and without awaiting
# ---------------------------------------------------------------------------


class TestValidatorIsUsableByAMigration:
    """⚠️ These tests exist for a caller that does not exist yet.

    The bridge migration cannot call `insert_event`: Alembic runs synchronously
    and `insert_event` is a coroutine that commits. It has to issue raw `INSERT`s
    instead — which skip every invariant unless the checks are reachable on their
    own. This class pins the two properties that make them reachable: the
    validator is SYNCHRONOUS, and it touches NO database.

    A future refactor that adds an `await` or a query here would pass every other
    test in this file and quietly put the migration back outside the rules.
    """

    def test_is_not_a_coroutine_function(self):
        import inspect

        assert not inspect.iscoroutinefunction(validate_pii_policy_event)

    def test_runs_with_no_session_and_no_event_loop(self):
        """Called bare — no fixture, no `await`, no patched session."""
        assert validate_pii_policy_event(EVENT_POLICY_ENABLED, "g1", "admin-1", "a@x.com") is None

    def test_takes_no_db_argument(self):
        import inspect

        assert "db" not in inspect.signature(validate_pii_policy_event).parameters


class TestValidatorRules:
    """Each invariant, exercised through the extracted function directly.

    The same rules are already covered through `insert_event`; these assert them
    on the seam the migration will use, so the two cannot drift apart.
    """

    def test_unknown_event_type(self):
        with pytest.raises(ValueError, match="unknown pii policy audit event_type"):
            validate_pii_policy_event("policy_maybe", "g1", "admin-1", "a@x.com")

    def test_member_event_requires_user_id(self):
        with pytest.raises(ValueError, match="requires user_id"):
            validate_pii_policy_event(EVENT_MEMBER_ADDED, "g1", "admin-1", "a@x.com")

    def test_policy_event_must_not_carry_user_id(self):
        with pytest.raises(ValueError, match="must not carry user_id"):
            validate_pii_policy_event(
                EVENT_POLICY_ENABLED, "g1", "admin-1", "a@x.com", user_id="u1"
            )

    def test_member_removed_requires_a_reason(self):
        with pytest.raises(ValueError, match="requires a reason"):
            validate_pii_policy_event(
                EVENT_MEMBER_REMOVED, "g1", "admin-1", "a@x.com", user_id="u1"
            )

    def test_whitespace_is_not_a_reason(self):
        with pytest.raises(ValueError, match="requires a reason"):
            validate_pii_policy_event(
                EVENT_MEMBER_REMOVED, "g1", "admin-1", "a@x.com", user_id="u1", reason="  \n "
            )

    def test_policy_disabled_requires_a_reason(self):
        with pytest.raises(ValueError, match="requires a reason"):
            validate_pii_policy_event(EVENT_POLICY_DISABLED, "g1", "admin-1", "a@x.com")

    def test_group_id_is_required(self):
        with pytest.raises(ValueError, match="group_id is required"):
            validate_pii_policy_event(EVENT_POLICY_ENABLED, "", "admin-1", "a@x.com")

    def test_actor_is_required(self):
        with pytest.raises(ValueError, match="actor_user_id and actor_email are required"):
            validate_pii_policy_event(EVENT_POLICY_ENABLED, "g1", "", "a@x.com")
        with pytest.raises(ValueError, match="actor_user_id and actor_email are required"):
            validate_pii_policy_event(EVENT_POLICY_ENABLED, "g1", "admin-1", "")

    def test_member_added_needs_no_reason(self):
        """Adding exposes nobody, so it asks nothing — the asymmetry, on this seam too."""
        assert (
            validate_pii_policy_event(EVENT_MEMBER_ADDED, "g1", "admin-1", "a@x.com", user_id="u1")
            is None
        )
