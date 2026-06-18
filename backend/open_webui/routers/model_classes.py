import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

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
    return ModelClasses.create(form_data)


@router.put("/{id}", response_model=ModelClassModel)
async def update_model_class(
    id: int, form_data: ModelClassUpdateForm, user=Depends(get_admin_user)
):
    result = ModelClasses.update(id, form_data)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model class not found",
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
