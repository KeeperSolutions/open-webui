import os

import aiohttp
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel

load_dotenv()
router = APIRouter()


CONFIDIOS_BASE_URL = os.getenv("CONFIDIOS_BASE_URL")


# Add response model
class ConfidiosLoginResponse(BaseModel):
    balance: str
    sid: str
    u: str


@router.post("/auths/login")
async def confidios_admin_login(user=Depends(get_verified_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can access this endpoint",
        )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/login/keeper",
                json={"password": "keeper"},  # Add payload here
            ) as response:
                if response.status != 200:
                    error_detail = (
                        "Failed to login to Confidios service"  # Updated error message
                    )
                    try:
                        error_body = await response.json()
                        error_detail = f"Status {response.status}: {error_body.get('detail', error_detail)}"
                    except Exception:
                        error_detail = (
                            f"Status {response.status}: {await response.text()}"
                        )

                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=error_detail,
                    )

                # Check content type
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    resp = await response.json()
                    # Validate response structure
                    confidios_resp = ConfidiosLoginResponse(**resp)
                else:
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail=f"Expected JSON response, got {content_type}",
                    )

                return {
                    "status": f"Confidios feature accessed by user: {user.email}",
                    "user_id": user.id,
                    "role": user.role,
                    "confidios_user": confidios_resp.u,
                    "confidios_balance": confidios_resp.balance,
                    "confidios_session_id": confidios_resp.sid,
                    "confidios_is_logged_in": True,
                }

    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
