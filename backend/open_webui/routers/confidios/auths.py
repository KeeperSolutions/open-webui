from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel

router = APIRouter()


# class YourFeatureModel(BaseModel):
#     name: str
#     description: Optional[str] = None


# @router.get("/auth/login")
# async def get_features(user=Depends(get_verified_user)):
#     # Your authenticated endpoint logic here
#     return {"features": []}

# todo: now only logged in admin users can access this endpoint


@router.post("/auths/login")
async def confidios_admin_login(user=Depends(get_verified_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can access this endpoint",
        )

    return {
        "status": f"confidios feature accessed by user: {user.email}",
        "user_id": user.id,
        "role": user.role,
    }
