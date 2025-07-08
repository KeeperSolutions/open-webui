from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from open_webui.utils.auth import get_verified_user
from .auths import confidios_sessions, CONFIDIOS_BASE_URL
import aiohttp
import json
import re

router = APIRouter()


class UserCreateRequest(BaseModel):
    user_id: str
    name: str
    email: str
    role: str
    profile_image_url: str


def clean_username(username: str) -> str:
    """Convert valid email to username-at-domain format."""
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"

    if re.match(email_pattern, username.strip()):
        return username.strip().replace("@", "-at-").lower()
    else:
        raise ValueError(f"Invalid email format: {username}")


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

    # Check if admin has active Confidios session
    if current_user.id not in confidios_sessions:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active Confidios session found. Please login first.",
        )

    try:
        identity = clean_username(user_data.email)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Get session data
    session_data = confidios_sessions[current_user.id]
    session_header = {
        "u": session_data["confidios_user"],
        "sid": session_data["confidios_session_id"],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/creat/user",
                headers={
                    "X-Confidios-Session-Id": json.dumps(session_header),
                    "Content-Type": "application/json",
                },
                json={
                    "identity": identity,
                    "password": "test-password",
                },  # Placeholder password
            ) as response:
                if response.status != 201:
                    error_detail = "Failed to create Confidios user"
                    try:
                        error_body = await response.json()
                        error_detail = f"{error_body.get('detail', error_detail)}"
                    except Exception:
                        error_detail = await response.text()

                    raise HTTPException(
                        status_code=response.status, detail=error_detail
                    )

                resp_data = await response.json()
                return {"confidios_user": resp_data}

    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process user data: {str(e)}",
        )
