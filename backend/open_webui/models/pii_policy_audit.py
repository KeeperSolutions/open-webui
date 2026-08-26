"""Audit log for administrative mutations of the team PII masking policy.

⚠️ This is deliberately NOT the PII *detection* audit trail. That one records PII
events (what was masked, when, in which chat) and lives with the pipeline; this
one records *administrative mutations* (who turned enforcement on, for which
group, why). Different readers, different retention, different lifetime — and
binding a governance mutation to the availability of the pipeline would mean the
policy cannot be changed exactly when it matters most.

⚠️ Writes here are BLOCKING, not best-effort. The detection trail's PII events
are best-effort on purpose: an audit outage must never block a chat. The opposite
rule applies to this table — a mutation in a compliance system with no record of
it is not acceptable, so `insert_event` lets its exceptions propagate and the
calling route rejects the mutation. Do not "align" the two by wrapping this in a
try/except; the divergence is the point.
"""

import logging
import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, get_async_db_context

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

####################
# Event types
####################

# The transition lives in the type, not in a pair of before/after booleans.
# `policy_enabled`/`policy_disabled` carry the direction themselves, so the only
# type-dependent nullable column left is `user_id`.
EVENT_POLICY_ENABLED = 'policy_enabled'
EVENT_POLICY_DISABLED = 'policy_disabled'
EVENT_MEMBER_ADDED = 'member_added'
EVENT_MEMBER_REMOVED = 'member_removed'

# The policy events are about a group; the member events are about one person's
# membership of it. `user_id` is required for exactly the latter pair.
POLICY_EVENT_TYPES = frozenset({EVENT_POLICY_ENABLED, EVENT_POLICY_DISABLED})
MEMBER_EVENT_TYPES = frozenset({EVENT_MEMBER_ADDED, EVENT_MEMBER_REMOVED})
EVENT_TYPES = POLICY_EVENT_TYPES | MEMBER_EVENT_TYPES

# Both are removals from protection, so both must say why. Turning protection ON
# exposes nobody, so there `reason` is free.
REASON_REQUIRED_EVENT_TYPES = frozenset({EVENT_POLICY_DISABLED, EVENT_MEMBER_REMOVED})


####################
# DB Schema
####################


class PiiPolicyAudit(Base):
    __tablename__ = 'pii_policy_audit'

    id = Column(Text, unique=True, primary_key=True)

    event_type = Column(Text, nullable=False)

    group_id = Column(Text, nullable=False)
    # NULL for policy_*, required for member_* — enforced by insert_event, not
    # by the DDL: a partial constraint is not portable across the databases this
    # runs on, so the invariant is guarded at the single writer and covered by
    # tests.
    user_id = Column(Text, nullable=True)

    actor_user_id = Column(Text, nullable=False)
    # Denormalised on purpose: the acting admin's account can be deleted, and an
    # audit row that can no longer say who acted has lost the thing it was for.
    actor_email = Column(Text, nullable=False)

    reason = Column(Text, nullable=True)

    event_ts = Column(BigInteger, nullable=False)

    # The primary read is one group's chronology ("enforcement on 3 Jan, Ana
    # added 5 Jan, off 9 Jan"), which is also why this is one table and not two.
    __table_args__ = (Index('pii_policy_audit_group_id_event_ts_idx', 'group_id', 'event_ts'),)


class PiiPolicyAuditModel(BaseModel):
    id: str
    event_type: str
    group_id: str
    user_id: Optional[str] = None
    actor_user_id: str
    actor_email: str
    reason: Optional[str] = None
    event_ts: int

    model_config = ConfigDict(from_attributes=True)


####################
# Table
####################


class PiiPolicyAuditTable:
    async def insert_event(
        self,
        event_type: str,
        group_id: str,
        actor_user_id: str,
        actor_email: str,
        user_id: Optional[str] = None,
        reason: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> PiiPolicyAuditModel:
        """Record one administrative mutation. Raises rather than returning None.

        Every other table in this codebase swallows write failures and returns
        None; this one must not. The caller's contract is "no record → no
        mutation", which it can only honour if a failed write is visible to it.

        Validation lives here rather than only in the route so the invariants
        hold for every future caller — the membership events go through this same
        door.
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f'unknown pii policy audit event_type: {event_type!r}')

        if event_type in MEMBER_EVENT_TYPES and not user_id:
            raise ValueError(f'{event_type} requires user_id')

        if event_type in POLICY_EVENT_TYPES and user_id:
            # Not cosmetic: a policy row carrying a user_id reads as "this
            # person's policy changed", which is a claim this feature never
            # makes — the policy is per group.
            raise ValueError(f'{event_type} must not carry user_id')

        if event_type in REASON_REQUIRED_EVENT_TYPES and not (reason or '').strip():
            raise ValueError(f'{event_type} requires a reason')

        if not group_id:
            raise ValueError('group_id is required')

        if not actor_user_id or not actor_email:
            raise ValueError('actor_user_id and actor_email are required')

        row = PiiPolicyAudit(
            id=str(uuid.uuid4()),
            event_type=event_type,
            group_id=group_id,
            user_id=user_id,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            reason=(reason or '').strip() or None,
            event_ts=int(time.time()),
        )

        async with get_async_db_context(db) as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return PiiPolicyAuditModel.model_validate(row)

    async def get_events_by_group_id(
        self,
        group_id: str,
        limit: Optional[int] = None,
        newest_first: bool = False,
        db: Optional[AsyncSession] = None,
    ) -> list[PiiPolicyAuditModel]:
        """One group's chronology — the read this table was shaped for.

        Oldest first by default, which is how the story reads. The panel asks for
        `newest_first` with a limit, because a cut has to drop the old tail and
        keep the fresh head; cutting the other way would hide exactly the changes
        someone opening the panel came to see.
        """
        order = PiiPolicyAudit.event_ts.desc() if newest_first else PiiPolicyAudit.event_ts.asc()
        async with get_async_db_context(db) as db:
            stmt = select(PiiPolicyAudit).filter(PiiPolicyAudit.group_id == group_id).order_by(order)
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await db.execute(stmt)
            return [PiiPolicyAuditModel.model_validate(row) for row in result.scalars().all()]

    async def count_events_by_group_id(self, group_id: str, db: Optional[AsyncSession] = None) -> int:
        """Total for this group, so a truncated page can say what it left out.

        Separate count rather than "did we hit the limit": the latter can only
        say "at least this many", and a panel that silently shows a slice is the
        thing the rest of this dashboard was built not to do.
        """
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(func.count()).select_from(PiiPolicyAudit).filter(PiiPolicyAudit.group_id == group_id)
            )
            return result.scalar()


PiiPolicyAudits = PiiPolicyAuditTable()
