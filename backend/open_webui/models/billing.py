import logging
import secrets
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Float, Integer, Text, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context

log = logging.getLogger(__name__)


####################
# StripeBilling DB Schema
####################


class StripeBilling(Base):
    __tablename__ = "stripe_billing"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, unique=True, nullable=False)

    stripe_customer_id = Column(Text, unique=True, nullable=True)
    stripe_subscription_id = Column(Text, unique=True, nullable=True)
    stripe_subscription_item_id = Column(Text, nullable=True)
    stripe_payment_method_id = Column(Text, nullable=True)

    subscription_status = Column(Text, nullable=True)  # active | past_due | canceled | incomplete
    free_tier_credit_applied = Column(Boolean, default=False, nullable=False)
    plan_tier = Column(Text, nullable=True)  # internal | trial | pro | premium | team | team_member
    team_id = Column(Text, nullable=True)  # set for plan_tier "team" and "team_member"

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class StripeBillingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str

    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    stripe_subscription_item_id: Optional[str] = None
    stripe_payment_method_id: Optional[str] = None

    subscription_status: Optional[str] = None
    free_tier_credit_applied: bool = False
    plan_tier: Optional[str] = None  # internal | trial | pro | premium | team | team_member
    team_id: Optional[str] = None

    created_at: int
    updated_at: int


####################
# Table accessor
####################


class StripeBillingTable:
    async def get_by_user_id(
        self, user_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[StripeBillingModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(StripeBilling).filter_by(user_id=user_id))
            row = result.scalars().first()
            return StripeBillingModel.model_validate(row) if row else None

    async def get_by_customer_id(
        self, customer_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[StripeBillingModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(StripeBilling).filter_by(stripe_customer_id=customer_id))
            row = result.scalars().first()
            return StripeBillingModel.model_validate(row) if row else None

    async def upsert(
        self,
        user_id: str,
        stripe_customer_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        stripe_subscription_item_id: Optional[str] = None,
        stripe_payment_method_id: Optional[str] = None,
        subscription_status: Optional[str] = None,
        free_tier_credit_applied: Optional[bool] = None,
        plan_tier: Optional[str] = None,
        team_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> StripeBillingModel:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(StripeBilling).filter_by(user_id=user_id))
            row = result.scalars().first()
            now = int(time.time())
            if row is None:
                row = StripeBilling(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                    stripe_subscription_item_id=stripe_subscription_item_id,
                    stripe_payment_method_id=stripe_payment_method_id,
                    subscription_status=subscription_status,
                    free_tier_credit_applied=free_tier_credit_applied or False,
                    plan_tier=plan_tier,
                    team_id=team_id,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                if stripe_customer_id is not None:
                    row.stripe_customer_id = stripe_customer_id
                if stripe_subscription_id is not None:
                    row.stripe_subscription_id = stripe_subscription_id
                if stripe_subscription_item_id is not None:
                    row.stripe_subscription_item_id = stripe_subscription_item_id
                if stripe_payment_method_id is not None:
                    row.stripe_payment_method_id = stripe_payment_method_id
                if subscription_status is not None:
                    row.subscription_status = subscription_status
                if plan_tier is not None:
                    row.plan_tier = plan_tier
                if free_tier_credit_applied is not None:
                    row.free_tier_credit_applied = free_tier_credit_applied
                if team_id is not None:
                    row.team_id = team_id
                row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return StripeBillingModel.model_validate(row)

    async def update_subscription_status(
        self, customer_id: str, status: str, db: Optional[AsyncSession] = None
    ) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(StripeBilling)
                .filter_by(stripe_customer_id=customer_id)
                .values(subscription_status=status, updated_at=int(time.time()))
            )
            await db.commit()
            return result.rowcount > 0

    async def get_all_active(self, db: Optional[AsyncSession] = None) -> list[StripeBillingModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(StripeBilling).filter_by(subscription_status="active"))
            return [StripeBillingModel.model_validate(r) for r in result.scalars().all()]

    async def get_all(self, db: Optional[AsyncSession] = None) -> list[StripeBillingModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(StripeBilling))
            return [StripeBillingModel.model_validate(r) for r in result.scalars().all()]

    async def get_team_members(
        self, team_id: str, db: Optional[AsyncSession] = None
    ) -> list[StripeBillingModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(StripeBilling).filter_by(team_id=team_id))
            return [StripeBillingModel.model_validate(r) for r in result.scalars().all()]

    async def revert_to_trial(self, user_id: str, db: Optional[AsyncSession] = None) -> None:
        """Remove team association and revert user to trial tier."""
        async with get_async_db_context(db) as db:
            result = await db.execute(select(StripeBilling).filter_by(user_id=user_id))
            row = result.scalars().first()
            if row:
                row.plan_tier = "trial"
                row.team_id = None
                row.updated_at = int(time.time())
                await db.commit()


StripeBillings = StripeBillingTable()


####################
# Team DB Schema
####################


class Team(Base):
    __tablename__ = "teams"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    owner_user_id = Column(Text, nullable=False)

    stripe_customer_id = Column(Text, unique=True, nullable=True)
    stripe_subscription_id = Column(Text, unique=True, nullable=True)
    stripe_subscription_item_id = Column(Text, nullable=True)
    stripe_payment_method_id = Column(Text, nullable=True)

    subscription_status = Column(Text, nullable=True)
    # The team's own PII policy group. Nullable and UNIQUE: nullable because a team
    # may not have one yet, UNIQUE because two teams pointing at one group would
    # make "the team's own group" ambiguous — see `utils/team_groups.py`.
    group_id = Column(Text, unique=True, nullable=True)
    seat_limit = Column(Integer, nullable=False, default=5)
    # How many subscription credits this plan includes per month (display only; authoritative value is in credit_balances)
    monthly_credits = Column(Integer, nullable=False, default=0)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class TeamModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_user_id: str

    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    stripe_subscription_item_id: Optional[str] = None
    stripe_payment_method_id: Optional[str] = None

    subscription_status: Optional[str] = None
    # ⚠️ Optional all the way out to the edge. A team without a group is a normal
    # state, not an error — narrowing this type is how a caller starts assuming
    # otherwise.
    group_id: Optional[str] = None
    seat_limit: int = 5
    monthly_credits: int = 0

    created_at: int
    updated_at: int


class TeamsTable:
    async def create(
        self,
        name: str,
        owner_user_id: str,
        seat_limit: int,
        monthly_credits: int = 0,
        db: Optional[AsyncSession] = None,
    ) -> TeamModel:
        async with get_async_db_context(db) as db:
            now = int(time.time())
            row = Team(
                id=str(uuid.uuid4()),
                name=name,
                owner_user_id=owner_user_id,
                seat_limit=seat_limit,
                monthly_credits=monthly_credits,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return TeamModel.model_validate(row)

    async def get_by_id(self, team_id: str, db: Optional[AsyncSession] = None) -> Optional[TeamModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Team).filter_by(id=team_id))
            row = result.scalars().first()
            return TeamModel.model_validate(row) if row else None

    async def get_by_owner_user_id(
        self, user_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[TeamModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Team).filter_by(owner_user_id=user_id))
            row = result.scalars().first()
            return TeamModel.model_validate(row) if row else None

    async def get_by_customer_id(
        self, customer_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[TeamModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Team).filter_by(stripe_customer_id=customer_id))
            row = result.scalars().first()
            return TeamModel.model_validate(row) if row else None

    async def get_all_active(self, db: Optional[AsyncSession] = None) -> list[TeamModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Team).filter(Team.subscription_status.in_(("active", "trialing")))
            )
            return [TeamModel.model_validate(r) for r in result.scalars().all()]

    async def update(
        self, team_id: str, db: Optional[AsyncSession] = None, **kwargs
    ) -> Optional[TeamModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Team).filter_by(id=team_id))
            row = result.scalars().first()
            if not row:
                return None
            for k, v in kwargs.items():
                if hasattr(row, k) and v is not None:
                    setattr(row, k, v)
            row.updated_at = int(time.time())
            await db.commit()
            await db.refresh(row)
            return TeamModel.model_validate(row)

    async def update_subscription_status(
        self, customer_id: str, status: str, db: Optional[AsyncSession] = None
    ) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(Team)
                .filter_by(stripe_customer_id=customer_id)
                .values(subscription_status=status, updated_at=int(time.time()))
            )
            await db.commit()
            return result.rowcount > 0


Teams = TeamsTable()


####################
# TeamMember DB Schema
####################


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(Text, primary_key=True)
    team_id = Column(Text, nullable=False)
    user_id = Column(Text, nullable=False)
    role = Column(Text, nullable=False, default="member")  # owner | member
    created_at = Column(BigInteger, nullable=False)


class TeamMemberModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    team_id: str
    user_id: str
    role: str = "member"
    created_at: int


class TeamMembersTable:
    async def add(
        self, team_id: str, user_id: str, role: str = "member", db: Optional[AsyncSession] = None
    ) -> TeamMemberModel:
        async with get_async_db_context(db) as db:
            row = TeamMember(
                id=str(uuid.uuid4()),
                team_id=team_id,
                user_id=user_id,
                role=role,
                created_at=int(time.time()),
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return TeamMemberModel.model_validate(row)

    async def get_by_team_id(
        self, team_id: str, db: Optional[AsyncSession] = None
    ) -> list[TeamMemberModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(TeamMember).filter_by(team_id=team_id))
            return [TeamMemberModel.model_validate(r) for r in result.scalars().all()]

    async def get_by_user_id(
        self, user_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[TeamMemberModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(TeamMember).filter_by(user_id=user_id))
            row = result.scalars().first()
            return TeamMemberModel.model_validate(row) if row else None

    async def members_among(
        self, team_id: str, user_ids: list[str], db: Optional[AsyncSession] = None
    ) -> set:
        """Which of these people are in this team. One query, whatever the count.

        Written for the authorisation guard, which has to answer "are ALL of these
        my team's members" for a whole request body. `get_by_team_id` would load
        the team and let the caller intersect in Python — correct, but it makes
        the cost depend on the team's size rather than on the request's, and the
        guard runs before every membership change.

        Hits `uq_team_members_team_user`, so it is an index lookup rather than a
        scan. Returns a set because the caller subtracts it; an empty input list
        returns an empty set without touching the database.
        """
        if not user_ids:
            return set()

        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(TeamMember.user_id).filter(
                    TeamMember.team_id == team_id, TeamMember.user_id.in_(user_ids)
                )
            )
            return {row for (row,) in result.all()}

    async def remove(self, team_id: str, user_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                delete(TeamMember).filter_by(team_id=team_id, user_id=user_id)
            )
            await db.commit()
            return result.rowcount > 0

    async def count_members(self, team_id: str, db: Optional[AsyncSession] = None) -> int:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(TeamMember).filter_by(team_id=team_id))
            return len(result.scalars().all())


TeamMembers = TeamMembersTable()


####################
# TeamInvite DB Schema
####################


class TeamInvite(Base):
    __tablename__ = "team_invites"

    id = Column(Text, primary_key=True)
    team_id = Column(Text, nullable=False)
    invited_email = Column(Text, nullable=False)
    invited_by = Column(Text, nullable=False)
    token = Column(Text, unique=True, nullable=False)
    status = Column(Text, nullable=False, default="pending")  # pending | accepted | declined | expired
    created_at = Column(BigInteger, nullable=False)
    expires_at = Column(BigInteger, nullable=False)


class TeamInviteModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    team_id: str
    invited_email: str
    invited_by: str
    token: str
    status: str = "pending"
    created_at: int
    expires_at: int


class TeamInvitesTable:
    async def create(
        self, team_id: str, invited_email: str, invited_by: str, db: Optional[AsyncSession] = None
    ) -> TeamInviteModel:
        async with get_async_db_context(db) as db:
            now = int(time.time())
            row = TeamInvite(
                id=str(uuid.uuid4()),
                team_id=team_id,
                invited_email=invited_email.lower(),
                invited_by=invited_by,
                token=secrets.token_urlsafe(32),
                status="pending",
                created_at=now,
                expires_at=now + 7 * 24 * 3600,  # 7 days
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return TeamInviteModel.model_validate(row)

    async def get_by_token(
        self, token: str, db: Optional[AsyncSession] = None
    ) -> Optional[TeamInviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(TeamInvite).filter_by(token=token))
            row = result.scalars().first()
            return TeamInviteModel.model_validate(row) if row else None

    async def get_by_team_id(
        self, team_id: str, db: Optional[AsyncSession] = None
    ) -> list[TeamInviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(TeamInvite).filter_by(team_id=team_id))
            return [TeamInviteModel.model_validate(r) for r in result.scalars().all()]

    async def get_pending_by_email(
        self, email: str, db: Optional[AsyncSession] = None
    ) -> list[TeamInviteModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(TeamInvite).filter_by(invited_email=email.lower(), status="pending")
            )
            return [TeamInviteModel.model_validate(r) for r in result.scalars().all()]

    async def update_status(
        self, token: str, status: str, db: Optional[AsyncSession] = None
    ) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(TeamInvite).filter_by(token=token).values(status=status)
            )
            await db.commit()
            return result.rowcount > 0

    async def delete_pending_by_email_and_team(
        self, team_id: str, email: str, db: Optional[AsyncSession] = None
    ) -> None:
        async with get_async_db_context(db) as db:
            await db.execute(
                delete(TeamInvite).filter_by(
                    team_id=team_id, invited_email=email.lower(), status="pending"
                )
            )
            await db.commit()


TeamInvites = TeamInvitesTable()
