import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Float, Integer, Text, UniqueConstraint

from open_webui.internal.db import Base, get_db


class CreditBalance(Base):
    __tablename__ = "credit_balances"

    id = Column(Text, primary_key=True)
    owner_type = Column(Text, nullable=False)   # 'user' | 'team'
    owner_id = Column(Text, nullable=False)     # user_id or team_id
    subscription_credits = Column(Integer, nullable=False, default=0)
    topup_credits = Column(Integer, nullable=False, default=0)
    credits_per_eur_cent = Column(Float, nullable=False, default=1.82)
    period_start = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", name="uq_credit_balances_owner"),
    )


class CreditBalanceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_type: str
    owner_id: str
    subscription_credits: int = 0
    topup_credits: int = 0
    credits_per_eur_cent: float = 1.82
    period_start: Optional[int] = None
    updated_at: int

    @property
    def total_credits(self) -> int:
        return self.subscription_credits + self.topup_credits


class CreditBalancesTable:
    def get(self, owner_type: str, owner_id: str) -> Optional[CreditBalanceModel]:
        with get_db() as db:
            row = db.query(CreditBalance).filter_by(owner_type=owner_type, owner_id=owner_id).first()
            return CreditBalanceModel.model_validate(row) if row else None

    def set_subscription(
        self,
        owner_type: str,
        owner_id: str,
        credits: int,
        credits_per_eur_cent: float,
        period_start: Optional[int] = None,
    ) -> CreditBalanceModel:
        """Set subscription credits (monthly reset). Does NOT touch topup_credits."""
        with get_db() as db:
            now = int(time.time())
            row = db.query(CreditBalance).filter_by(owner_type=owner_type, owner_id=owner_id).first()
            if row is None:
                row = CreditBalance(
                    id=str(uuid.uuid4()),
                    owner_type=owner_type,
                    owner_id=owner_id,
                    subscription_credits=credits,
                    topup_credits=0,
                    credits_per_eur_cent=credits_per_eur_cent,
                    period_start=period_start or now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.subscription_credits = credits
                row.credits_per_eur_cent = credits_per_eur_cent
                if period_start is not None:
                    row.period_start = period_start
                row.updated_at = now
            db.commit()
            db.refresh(row)
            return CreditBalanceModel.model_validate(row)

    def add_topup(self, owner_type: str, owner_id: str, credits: int) -> Optional[CreditBalanceModel]:
        """Add top-up credits. Does NOT touch subscription_credits."""
        with get_db() as db:
            row = db.query(CreditBalance).filter_by(owner_type=owner_type, owner_id=owner_id).first()
            if row is None:
                return None
            row.topup_credits = (row.topup_credits or 0) + credits
            row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return CreditBalanceModel.model_validate(row)

    def reset_topup(self, owner_type: str, owner_id: str) -> None:
        """Zero out top-up credits (on subscription cancellation)."""
        with get_db() as db:
            db.query(CreditBalance).filter_by(owner_type=owner_type, owner_id=owner_id).update(
                {"topup_credits": 0, "updated_at": int(time.time())}
            )
            db.commit()

    def reset_all(self, owner_type: str, owner_id: str) -> None:
        """Zero both credit fields (on subscription cancellation)."""
        with get_db() as db:
            db.query(CreditBalance).filter_by(owner_type=owner_type, owner_id=owner_id).update(
                {"subscription_credits": 0, "topup_credits": 0, "updated_at": int(time.time())}
            )
            db.commit()

    def upsert_trial(
        self,
        owner_id: str,
        credits: int,
        credits_per_eur_cent: float,
    ) -> CreditBalanceModel:
        """Create or update a trial user's balance. Only sets if no row exists yet."""
        with get_db() as db:
            now = int(time.time())
            row = db.query(CreditBalance).filter_by(owner_type="user", owner_id=owner_id).first()
            if row is None:
                row = CreditBalance(
                    id=str(uuid.uuid4()),
                    owner_type="user",
                    owner_id=owner_id,
                    subscription_credits=credits,
                    topup_credits=0,
                    credits_per_eur_cent=credits_per_eur_cent,
                    period_start=now,
                    updated_at=now,
                )
                db.add(row)
                db.commit()
                db.refresh(row)
            return CreditBalanceModel.model_validate(row)


CreditBalances = CreditBalancesTable()
