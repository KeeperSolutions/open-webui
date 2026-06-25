import logging
import secrets
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Float, Integer, Text

from open_webui.internal.db import Base, get_db

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
    plan_tier = Column(Text, nullable=True)  # internal | trial | paid | team | team_member
    checkout_session_id = Column(Text, nullable=True)
    topup_checkout_session_id = Column(Text, nullable=True)
    team_id = Column(Text, nullable=True)  # set for plan_tier "team" and "team_member"

    top_up_credits = Column(Integer, nullable=False, default=0)

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
    plan_tier: Optional[str] = None  # internal | trial | paid | team | team_member
    checkout_session_id: Optional[str] = None
    topup_checkout_session_id: Optional[str] = None
    team_id: Optional[str] = None
    top_up_credits: int = 0

    created_at: int
    updated_at: int


####################
# Table accessor
####################


class StripeBillingTable:
    def get_by_user_id(self, user_id: str) -> Optional[StripeBillingModel]:
        with get_db() as db:
            row = db.query(StripeBilling).filter_by(user_id=user_id).first()
            return StripeBillingModel.model_validate(row) if row else None

    def get_by_checkout_session_id(self, session_id: str) -> Optional[StripeBillingModel]:
        with get_db() as db:
            row = db.query(StripeBilling).filter_by(checkout_session_id=session_id).first()
            return StripeBillingModel.model_validate(row) if row else None

    def get_by_topup_checkout_session_id(self, session_id: str) -> Optional[StripeBillingModel]:
        with get_db() as db:
            row = db.query(StripeBilling).filter_by(topup_checkout_session_id=session_id).first()
            return StripeBillingModel.model_validate(row) if row else None

    def update_topup_checkout_session_id(self, user_id: str, session_id: str) -> bool:
        with get_db() as db:
            updated = (
                db.query(StripeBilling)
                .filter_by(user_id=user_id)
                .update({"topup_checkout_session_id": session_id, "updated_at": int(time.time())})
            )
            db.commit()
            return updated > 0

    def get_by_customer_id(self, customer_id: str) -> Optional[StripeBillingModel]:
        with get_db() as db:
            row = db.query(StripeBilling).filter_by(stripe_customer_id=customer_id).first()
            return StripeBillingModel.model_validate(row) if row else None

    def upsert(
        self,
        user_id: str,
        stripe_customer_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        stripe_subscription_item_id: Optional[str] = None,
        stripe_payment_method_id: Optional[str] = None,
        subscription_status: Optional[str] = None,
        free_tier_credit_applied: Optional[bool] = None,
        plan_tier: Optional[str] = None,
        checkout_session_id: Optional[str] = None,
        topup_checkout_session_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> StripeBillingModel:
        with get_db() as db:
            row = db.query(StripeBilling).filter_by(user_id=user_id).first()
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
                    checkout_session_id=checkout_session_id,
                    topup_checkout_session_id=topup_checkout_session_id,
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
                if checkout_session_id is not None:
                    row.checkout_session_id = checkout_session_id
                if topup_checkout_session_id is not None:
                    row.topup_checkout_session_id = topup_checkout_session_id
                if free_tier_credit_applied is not None:
                    row.free_tier_credit_applied = free_tier_credit_applied
                if team_id is not None:
                    row.team_id = team_id
                row.updated_at = now
            db.commit()
            db.refresh(row)
            return StripeBillingModel.model_validate(row)

    def update_subscription_status(self, customer_id: str, status: str) -> bool:
        with get_db() as db:
            updated = (
                db.query(StripeBilling)
                .filter_by(stripe_customer_id=customer_id)
                .update({"subscription_status": status, "updated_at": int(time.time())})
            )
            db.commit()
            return updated > 0

    def get_all_active(self) -> list[StripeBillingModel]:
        with get_db() as db:
            rows = db.query(StripeBilling).filter_by(subscription_status="active").all()
            return [StripeBillingModel.model_validate(r) for r in rows]

    def get_all(self) -> list[StripeBillingModel]:
        with get_db() as db:
            rows = db.query(StripeBilling).all()
            return [StripeBillingModel.model_validate(r) for r in rows]

    def get_team_members(self, team_id: str) -> list[StripeBillingModel]:
        with get_db() as db:
            rows = db.query(StripeBilling).filter_by(team_id=team_id).all()
            return [StripeBillingModel.model_validate(r) for r in rows]

    def revert_to_trial(self, user_id: str) -> None:
        """Remove team association and revert user to trial tier."""
        with get_db() as db:
            row = db.query(StripeBilling).filter_by(user_id=user_id).first()
            if row:
                row.plan_tier = "trial"
                row.team_id = None
                row.updated_at = int(time.time())
                db.commit()

    def add_top_up_credits(self, user_id: str, credits: int) -> Optional[StripeBillingModel]:
        with get_db() as db:
            row = db.query(StripeBilling).filter_by(user_id=user_id).first()
            if not row:
                return None
            row.top_up_credits = (row.top_up_credits or 0) + credits
            row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return StripeBillingModel.model_validate(row)

    def reset_top_up_credits(self, user_id: str) -> None:
        with get_db() as db:
            db.query(StripeBilling).filter_by(user_id=user_id).update(
                {"top_up_credits": 0, "updated_at": int(time.time())}
            )
            db.commit()


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
    seat_limit = Column(Integer, nullable=False, default=5)
    checkout_session_id = Column(Text, nullable=True)
    topup_checkout_session_id = Column(Text, nullable=True)

    # Usage budget included with the flat subscription
    monthly_usage_budget_eur = Column(Float, nullable=False, default=0.0)
    # Extra credits purchased via top-up; owner can buy more when budget is exhausted
    top_up_credits = Column(Integer, nullable=False, default=0)

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
    seat_limit: int = 5
    checkout_session_id: Optional[str] = None
    topup_checkout_session_id: Optional[str] = None

    monthly_usage_budget_eur: float = 0.0
    top_up_credits: int = 0

    created_at: int
    updated_at: int


class TeamsTable:
    def create(
        self,
        name: str,
        owner_user_id: str,
        seat_limit: int,
        monthly_usage_budget_eur: float = 0.0,
    ) -> TeamModel:
        with get_db() as db:
            now = int(time.time())
            row = Team(
                id=str(uuid.uuid4()),
                name=name,
                owner_user_id=owner_user_id,
                seat_limit=seat_limit,
                monthly_usage_budget_eur=monthly_usage_budget_eur,
                top_up_credits=0,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return TeamModel.model_validate(row)

    def get_by_id(self, team_id: str) -> Optional[TeamModel]:
        with get_db() as db:
            row = db.query(Team).filter_by(id=team_id).first()
            return TeamModel.model_validate(row) if row else None

    def get_by_owner_user_id(self, user_id: str) -> Optional[TeamModel]:
        with get_db() as db:
            row = db.query(Team).filter_by(owner_user_id=user_id).first()
            return TeamModel.model_validate(row) if row else None

    def get_by_checkout_session_id(self, session_id: str) -> Optional[TeamModel]:
        with get_db() as db:
            row = db.query(Team).filter_by(checkout_session_id=session_id).first()
            return TeamModel.model_validate(row) if row else None

    def get_by_customer_id(self, customer_id: str) -> Optional[TeamModel]:
        with get_db() as db:
            row = db.query(Team).filter_by(stripe_customer_id=customer_id).first()
            return TeamModel.model_validate(row) if row else None

    def get_all_active(self) -> list[TeamModel]:
        with get_db() as db:
            rows = db.query(Team).filter(
                Team.subscription_status.in_(("active", "trialing"))
            ).all()
            return [TeamModel.model_validate(r) for r in rows]

    def update(self, team_id: str, **kwargs) -> Optional[TeamModel]:
        with get_db() as db:
            row = db.query(Team).filter_by(id=team_id).first()
            if not row:
                return None
            for k, v in kwargs.items():
                if hasattr(row, k) and v is not None:
                    setattr(row, k, v)
            row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return TeamModel.model_validate(row)

    def update_subscription_status(self, customer_id: str, status: str) -> bool:
        with get_db() as db:
            updated = (
                db.query(Team)
                .filter_by(stripe_customer_id=customer_id)
                .update({"subscription_status": status, "updated_at": int(time.time())})
            )
            db.commit()
            return updated > 0

    def add_top_up_credits(self, team_id: str, credits: int) -> Optional[TeamModel]:
        with get_db() as db:
            row = db.query(Team).filter_by(id=team_id).first()
            if not row:
                return None
            row.top_up_credits = (row.top_up_credits or 0) + credits
            row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return TeamModel.model_validate(row)

    def reset_top_up_credits(self, team_id: str) -> None:
        with get_db() as db:
            db.query(Team).filter_by(id=team_id).update(
                {"top_up_credits": 0, "updated_at": int(time.time())}
            )
            db.commit()

    def get_by_topup_checkout_session_id(self, session_id: str) -> Optional[TeamModel]:
        with get_db() as db:
            row = db.query(Team).filter_by(topup_checkout_session_id=session_id).first()
            return TeamModel.model_validate(row) if row else None


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
    def add(self, team_id: str, user_id: str, role: str = "member") -> TeamMemberModel:
        with get_db() as db:
            row = TeamMember(
                id=str(uuid.uuid4()),
                team_id=team_id,
                user_id=user_id,
                role=role,
                created_at=int(time.time()),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return TeamMemberModel.model_validate(row)

    def get_by_team_id(self, team_id: str) -> list[TeamMemberModel]:
        with get_db() as db:
            rows = db.query(TeamMember).filter_by(team_id=team_id).all()
            return [TeamMemberModel.model_validate(r) for r in rows]

    def get_by_user_id(self, user_id: str) -> Optional[TeamMemberModel]:
        with get_db() as db:
            row = db.query(TeamMember).filter_by(user_id=user_id).first()
            return TeamMemberModel.model_validate(row) if row else None

    def remove(self, team_id: str, user_id: str) -> bool:
        with get_db() as db:
            deleted = (
                db.query(TeamMember)
                .filter_by(team_id=team_id, user_id=user_id)
                .delete()
            )
            db.commit()
            return deleted > 0

    def count_members(self, team_id: str) -> int:
        with get_db() as db:
            return db.query(TeamMember).filter_by(team_id=team_id).count()


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
    def create(self, team_id: str, invited_email: str, invited_by: str) -> TeamInviteModel:
        with get_db() as db:
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
            db.commit()
            db.refresh(row)
            return TeamInviteModel.model_validate(row)

    def get_by_token(self, token: str) -> Optional[TeamInviteModel]:
        with get_db() as db:
            row = db.query(TeamInvite).filter_by(token=token).first()
            return TeamInviteModel.model_validate(row) if row else None

    def get_by_team_id(self, team_id: str) -> list[TeamInviteModel]:
        with get_db() as db:
            rows = db.query(TeamInvite).filter_by(team_id=team_id).all()
            return [TeamInviteModel.model_validate(r) for r in rows]

    def get_pending_by_email(self, email: str) -> list[TeamInviteModel]:
        with get_db() as db:
            rows = (
                db.query(TeamInvite)
                .filter_by(invited_email=email.lower(), status="pending")
                .all()
            )
            return [TeamInviteModel.model_validate(r) for r in rows]

    def update_status(self, token: str, status: str) -> bool:
        with get_db() as db:
            updated = db.query(TeamInvite).filter_by(token=token).update({"status": status})
            db.commit()
            return updated > 0

    def delete_pending_by_email_and_team(self, team_id: str, email: str) -> None:
        with get_db() as db:
            db.query(TeamInvite).filter_by(
                team_id=team_id, invited_email=email.lower(), status="pending"
            ).delete()
            db.commit()


TeamInvites = TeamInvitesTable()
