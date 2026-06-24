import logging
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Float, Integer, JSON, Text

from open_webui.internal.db import Base, get_db

log = logging.getLogger(__name__)


####################
# ModelClass DB Schema
####################


class ModelClass(Base):
    __tablename__ = "model_class"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    models = Column(JSON, nullable=True)
    credit_burn = Column(Float, nullable=False)
    msgs_pro = Column(Text, nullable=True)
    msgs_premium = Column(Text, nullable=True)
    msgs_business = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    order = Column(Integer, nullable=False, unique=True)


class ModelClassModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    models: Optional[list[str]] = None
    credit_burn: float
    msgs_pro: Optional[str] = None
    msgs_premium: Optional[str] = None
    msgs_business: Optional[str] = None
    created_at: int
    updated_at: int
    order: int


####################
# Forms
####################


class ModelClassForm(BaseModel):
    name: str
    models: Optional[list[str]] = None
    credit_burn: float
    msgs_pro: Optional[str] = None
    msgs_premium: Optional[str] = None
    msgs_business: Optional[str] = None
    order: Optional[int] = None


class ModelClassUpdateForm(ModelClassForm):
    pass


####################
# Table accessor
####################


class ModelClassesTable:
    def get_all(self) -> list[ModelClassModel]:
        with get_db() as db:
            rows = db.query(ModelClass).order_by(ModelClass.order).all()
            return [ModelClassModel.model_validate(r) for r in rows]

    def get_by_id(self, id: int) -> Optional[ModelClassModel]:
        with get_db() as db:
            row = db.query(ModelClass).filter_by(id=id).first()
            return ModelClassModel.model_validate(row) if row else None

    def create(self, form_data: ModelClassForm) -> ModelClassModel:
        with get_db() as db:
            now = int(time.time())
            if form_data.order is None:
                max_order = db.query(ModelClass.order).order_by(ModelClass.order.desc()).limit(1).scalar() or 0
                order_value = max_order + 1
            else:
                order_value = form_data.order

            row = ModelClass(
                name=form_data.name,
                models=form_data.models,
                credit_burn=form_data.credit_burn,
                msgs_pro=form_data.msgs_pro,
                msgs_premium=form_data.msgs_premium,
                msgs_business=form_data.msgs_business,
                created_at=now,
                updated_at=now,
                order=order_value,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return ModelClassModel.model_validate(row)

    def update(self, id: int, form_data: ModelClassUpdateForm) -> ModelClassModel:
        with get_db() as db:
            row = db.query(ModelClass).filter_by(id=id).first()
            if not row:
                # Should not happen because router already checks existence
                raise RuntimeError(f"ModelClass with id={id} disappeared during update")
            if form_data.order is not None:
                row.order = form_data.order
            row.name = form_data.name
            row.models = form_data.models
            row.credit_burn = form_data.credit_burn
            row.msgs_pro = form_data.msgs_pro
            row.msgs_premium = form_data.msgs_premium
            row.msgs_business = form_data.msgs_business
            row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return ModelClassModel.model_validate(row)

    def delete(self, id: int) -> bool:
        with get_db() as db:
            row = db.query(ModelClass).filter_by(id=id).first()
            if not row:
                return False
            db.delete(row)
            db.commit()
            return True


ModelClasses = ModelClassesTable()
