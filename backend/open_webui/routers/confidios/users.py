from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from open_webui.utils.auth import get_verified_user

router = APIRouter()


class UserCreateRequest(BaseModel):
    user_id: str
    name: str
    email: str
    role: str
    profile_image_url: str


@router.post("/create")
async def create_confidios_user(
    user_data: UserCreateRequest, current_user=Depends(get_verified_user)
):
    # Check if user is admin
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can create Confidios users",
        )

    try:
        # For now, just return the received data
        return {"status": "Received user data", "user": user_data.dict()}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process user data: {str(e)}",
        )
