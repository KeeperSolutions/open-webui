import logging
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Text

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
    plan_tier = Column(Text, nullable=True)  # internal | trial | paid
    checkout_session_id = Column(Text, nullable=True)

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
    plan_tier: Optional[str] = None  # internal | trial | paid
    checkout_session_id: Optional[str] = None

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
                if free_tier_credit_applied is not None:
                    row.free_tier_credit_applied = free_tier_credit_applied
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


StripeBillings = StripeBillingTable()
