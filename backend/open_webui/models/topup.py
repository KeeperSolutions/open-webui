from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Float, Integer, Text

from open_webui.internal.db import Base, get_db


class TopupPack(Base):
    __tablename__ = "topup_packs"

    id = Column(Text, primary_key=True)
    credits = Column(Integer, nullable=False)
    price_eur = Column(Float, nullable=False)
    stripe_price_id = Column(Text, nullable=False, unique=True)
    created_at = Column(BigInteger, nullable=False)


class TopupPackModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    credits: int
    price_eur: float
    stripe_price_id: str
    created_at: int


class TopupPacksTable:
    def get_all(self) -> list[TopupPackModel]:
        with get_db() as db:
            rows = db.query(TopupPack).order_by(TopupPack.credits).all()
            return [TopupPackModel.model_validate(r) for r in rows]

    def get_by_id(self, pack_id: str) -> Optional[TopupPackModel]:
        with get_db() as db:
            row = db.query(TopupPack).filter_by(id=pack_id).first()
            return TopupPackModel.model_validate(row) if row else None


TopupPacks = TopupPacksTable()
