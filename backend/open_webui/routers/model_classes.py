import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from open_webui.models.model_classes import (
    ModelClassForm,
    ModelClassModel,
    ModelClassUpdateForm,
    ModelClasses,
)
from open_webui.utils.auth import get_admin_user

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=list[ModelClassModel])
async def get_all_model_classes():
    return ModelClasses.get_all()


@router.post("/", response_model=ModelClassModel)
async def create_model_class(
    form_data: ModelClassForm, user=Depends(get_admin_user)
):
    try:
        result = ModelClasses.create(form_data)
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order value already exists",
        )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order value already exists",
        )
    return result


@router.put("/{id}", response_model=ModelClassModel)
async def update_model_class(
    id: int, form_data: ModelClassUpdateForm, user=Depends(get_admin_user)
):
    existing = ModelClasses.get_by_id(id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model class not found",
        )

    try:
        result = ModelClasses.update(id, form_data)
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order value already exists",
        )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order value already exists",
        )
    return result


@router.delete("/{id}")
async def delete_model_class(id: int, user=Depends(get_admin_user)):
    success = ModelClasses.delete(id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model class not found",
        )
    return {"message": "Model class deleted"}


class ReorderItem(BaseModel):
    id: int
    order: int


@router.post("/reorder", response_model=list[ModelClassModel])
async def reorder_model_classes(
    items: List[ReorderItem], user=Depends(get_admin_user)
):
    from open_webui.internal.db import get_db
    from open_webui.models.model_classes import ModelClass
    import time

    if not items:
        return ModelClasses.get_all()

    ids = [item.id for item in items]
    orders = [item.order for item in items]

    if len(ids) != len(set(ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate IDs in reorder request",
        )

    if len(orders) != len(set(orders)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate order values in reorder request",
        )

    existing = ModelClasses.get_all()
    existing_ids = {row.id for row in existing}
    if not all(i in existing_ids for i in ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more model class IDs not found",
        )

    with get_db() as db:
        now = int(time.time())
        try:
            # First pass: move all rows to temporary negative orders to avoid unique conflicts
            for idx, item in enumerate(items):
                db.query(ModelClass).filter_by(id=item.id).update(
                    {"order": -(idx + 1), "updated_at": now}
                )
            db.flush()
            # Second pass: set final positive orders
            for item in items:
                db.query(ModelClass).filter_by(id=item.id).update(
                    {"order": item.order, "updated_at": now}
                )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate order value",
            )

    return ModelClasses.get_all()
