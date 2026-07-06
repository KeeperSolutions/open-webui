from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Float, Integer, Text

from open_webui.internal.db import Base, get_db


class StripePackage(Base):
    __tablename__ = "stripe_packages"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    plan_tier = Column(Text, nullable=False)  # pro | premium | team
    stripe_price_id = Column(Text, nullable=False, unique=True)
    price_eur = Column(Float, nullable=False)
    credits = Column(Integer, nullable=False)  # monthly allocation
    seat_count = Column(Integer, nullable=True)  # team only
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(BigInteger, nullable=False)


class StripePackageModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    plan_tier: str
    stripe_price_id: str
    price_eur: float
    credits: int
    seat_count: Optional[int] = None
    is_active: bool
    created_at: int


class StripePackagesTable:
    def get_all(self) -> list[StripePackageModel]:
        with get_db() as db:
            rows = db.query(StripePackage).filter_by(is_active=True).order_by(StripePackage.price_eur).all()
            return [StripePackageModel.model_validate(r) for r in rows]

    def get_all_by_tier(self, plan_tier: str) -> list[StripePackageModel]:
        with get_db() as db:
            rows = (
                db.query(StripePackage)
                .filter_by(plan_tier=plan_tier, is_active=True)
                .order_by(StripePackage.price_eur)
                .all()
            )
            return [StripePackageModel.model_validate(r) for r in rows]

    def get_by_tier(self, plan_tier: str) -> Optional[StripePackageModel]:
        """Return the first active package for a tier (for single-sku tiers like pro/premium)."""
        with get_db() as db:
            row = db.query(StripePackage).filter_by(plan_tier=plan_tier, is_active=True).first()
            return StripePackageModel.model_validate(row) if row else None

    def get_by_price_id(self, stripe_price_id: str) -> Optional[StripePackageModel]:
        with get_db() as db:
            row = db.query(StripePackage).filter_by(stripe_price_id=stripe_price_id).first()
            return StripePackageModel.model_validate(row) if row else None

    def get_by_id(self, package_id: str) -> Optional[StripePackageModel]:
        with get_db() as db:
            row = db.query(StripePackage).filter_by(id=package_id).first()
            return StripePackageModel.model_validate(row) if row else None

    def upsert(
        self,
        id: str,
        name: str,
        plan_tier: str,
        stripe_price_id: str,
        price_eur: float,
        credits: int,
        seat_count: Optional[int] = None,
        is_active: bool = True,
        created_at: Optional[int] = None,
    ) -> StripePackageModel:
        import time

        with get_db() as db:
            row = db.query(StripePackage).filter_by(id=id).first()
            if row is None:
                row = StripePackage(
                    id=id,
                    name=name,
                    plan_tier=plan_tier,
                    stripe_price_id=stripe_price_id,
                    price_eur=price_eur,
                    credits=credits,
                    seat_count=seat_count,
                    is_active=is_active,
                    created_at=created_at or int(time.time()),
                )
                db.add(row)
            else:
                row.name = name
                row.plan_tier = plan_tier
                row.stripe_price_id = stripe_price_id
                row.price_eur = price_eur
                row.credits = credits
                row.seat_count = seat_count
                row.is_active = is_active
            db.commit()
            db.refresh(row)
            return StripePackageModel.model_validate(row)

    def delete(self, package_id: str) -> bool:
        with get_db() as db:
            row = db.query(StripePackage).filter_by(id=package_id).first()
            if row:
                db.delete(row)
                db.commit()
                return True
            return False


StripePackages = StripePackagesTable()
