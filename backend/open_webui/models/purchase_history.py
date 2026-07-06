import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Float, Index, Integer, Text

from open_webui.internal.db import Base, get_db


class StripePurchaseHistory(Base):
    __tablename__ = "stripe_purchase_history"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False, index=True)
    team_id = Column(Text, nullable=True, index=True)
    stripe_customer_id = Column(Text, nullable=True)
    stripe_subscription_id = Column(Text, nullable=True)
    stripe_checkout_session_id = Column(Text, nullable=True, unique=True)
    stripe_invoice_id = Column(Text, nullable=True, unique=True)
    # subscription_start | renewal | topup | cancellation
    event_type = Column(Text, nullable=False)
    plan_tier = Column(Text, nullable=True)
    package_id = Column(Text, nullable=True)  # FK → stripe_packages.id
    subscription_credits_granted = Column(Integer, nullable=False, default=0)
    topup_credits_granted = Column(Integer, nullable=False, default=0)
    amount_eur = Column(Float, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class StripePurchaseHistoryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    team_id: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    stripe_checkout_session_id: Optional[str] = None
    stripe_invoice_id: Optional[str] = None
    event_type: str
    plan_tier: Optional[str] = None
    package_id: Optional[str] = None
    subscription_credits_granted: int = 0
    topup_credits_granted: int = 0
    amount_eur: Optional[float] = None
    created_at: int


class PurchaseHistoryTable:
    def insert(
        self,
        user_id: str,
        event_type: str,
        team_id: Optional[str] = None,
        stripe_customer_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        stripe_checkout_session_id: Optional[str] = None,
        stripe_invoice_id: Optional[str] = None,
        plan_tier: Optional[str] = None,
        package_id: Optional[str] = None,
        subscription_credits_granted: int = 0,
        topup_credits_granted: int = 0,
        amount_eur: Optional[float] = None,
    ) -> StripePurchaseHistoryModel:
        with get_db() as db:
            row = StripePurchaseHistory(
                id=str(uuid.uuid4()),
                user_id=user_id,
                team_id=team_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=stripe_subscription_id,
                stripe_checkout_session_id=stripe_checkout_session_id,
                stripe_invoice_id=stripe_invoice_id,
                event_type=event_type,
                plan_tier=plan_tier,
                package_id=package_id,
                subscription_credits_granted=subscription_credits_granted,
                topup_credits_granted=topup_credits_granted,
                amount_eur=amount_eur,
                created_at=int(time.time()),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return StripePurchaseHistoryModel.model_validate(row)

    def already_processed(
        self,
        stripe_checkout_session_id: Optional[str] = None,
        stripe_invoice_id: Optional[str] = None,
    ) -> bool:
        """Idempotency check — returns True if this event was already recorded."""
        with get_db() as db:
            if stripe_checkout_session_id:
                exists = db.query(StripePurchaseHistory).filter_by(
                    stripe_checkout_session_id=stripe_checkout_session_id
                ).first()
                if exists:
                    return True
            if stripe_invoice_id:
                exists = db.query(StripePurchaseHistory).filter_by(
                    stripe_invoice_id=stripe_invoice_id
                ).first()
                if exists:
                    return True
            return False

    def get_by_user(self, user_id: str) -> list[StripePurchaseHistoryModel]:
        with get_db() as db:
            rows = (
                db.query(StripePurchaseHistory)
                .filter_by(user_id=user_id)
                .order_by(StripePurchaseHistory.created_at.desc())
                .all()
            )
            return [StripePurchaseHistoryModel.model_validate(r) for r in rows]

    def get_by_team(self, team_id: str) -> list[StripePurchaseHistoryModel]:
        with get_db() as db:
            rows = (
                db.query(StripePurchaseHistory)
                .filter_by(team_id=team_id)
                .order_by(StripePurchaseHistory.created_at.desc())
                .all()
            )
            return [StripePurchaseHistoryModel.model_validate(r) for r in rows]


PurchaseHistory = PurchaseHistoryTable()
