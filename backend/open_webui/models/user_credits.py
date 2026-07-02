import os
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Float, Index, Integer, Text

from open_webui.internal.db import Base, get_db
from open_webui.models.billing_plans import PLAN_TIER_TRIAL, get_trial_credits  # noqa: F401 — re-exported for callers


def _load_credits_per_eur_cent() -> float:
    val = os.environ.get("CREDITS_PER_EUR_CENT")
    if val is None:
        raise RuntimeError("CREDITS_PER_EUR_CENT env var is required but not set")
    return float(val)


CREDITS_PER_EUR_CENT: float = _load_credits_per_eur_cent()


def eur_to_credits(eur: float, rate: float) -> int:
    return round(eur * 100 * rate)


def credits_to_eur(credits: int, rate: float) -> float:
    return round(credits / (100 * rate), 4) if rate > 0 else 0.0


####################
# UserCredits DB Schema
####################


class UserCredits(Base):
    __tablename__ = "user_credits"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, unique=True, nullable=False)
    balance = Column(Integer, default=0, nullable=False)
    credits_per_eur_cent = Column(Float, default=1.82, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index("ix_user_credits_user_id", "user_id"),)


class UserCreditsModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    balance: int
    credits_per_eur_cent: float
    updated_at: int


####################
# Table accessor
####################


class UserCreditsTable:
    def get(self, user_id: str) -> Optional[UserCreditsModel]:
        with get_db() as db:
            row = db.query(UserCredits).filter_by(user_id=user_id).first()
            return UserCreditsModel.model_validate(row) if row else None

    def get_balance(self, user_id: str) -> int:
        row = self.get(user_id)
        return row.balance if row else 0

    def add_credits(self, user_id: str, amount: int) -> int:
        """Atomic increment. Does not change credits_per_eur_cent. Returns new balance."""
        with get_db() as db:
            row = db.query(UserCredits).filter_by(user_id=user_id).first()
            if row is None:
                row = UserCredits(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    balance=amount,
                    credits_per_eur_cent=CREDITS_PER_EUR_CENT,
                    updated_at=int(time.time()),
                )
                db.add(row)
            else:
                row.balance += amount
                row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return row.balance

    def set_plan(self, user_id: str, balance: int, credits_per_eur_cent: float) -> UserCreditsModel:
        """Upsert — resets balance and locks the rate. Used on plan assignment and monthly renewal."""
        with get_db() as db:
            row = db.query(UserCredits).filter_by(user_id=user_id).first()
            if row is None:
                row = UserCredits(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    balance=balance,
                    credits_per_eur_cent=credits_per_eur_cent,
                    updated_at=int(time.time()),
                )
                db.add(row)
            else:
                row.balance = balance
                row.credits_per_eur_cent = credits_per_eur_cent
                row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return UserCreditsModel.model_validate(row)


UserCreditsDB = UserCreditsTable()
