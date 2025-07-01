import os

import aiohttp
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from open_webui.utils.auth import get_verified_user

load_dotenv()
router = APIRouter()


CONFIDIOS_BASE_URL = os.getenv("CONFIDIOS_BASE_URL")


@router.post("/auths/login")
async def confidios_admin_login(user=Depends(get_verified_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can access this endpoint",
        )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{CONFIDIOS_BASE_URL}/apispec/json/eudr"
            ) as response:
                if response.status != 200:
                    error_detail = "Failed to fetch API spec from Confidios service"
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
                    api_spec = await response.json()
                else:
                    # Handle plain text response
                    api_spec = await response.text()

                return {
                    "status": f"confidios feature accessed by user: {user.email}",
                    "user_id": user.id,
                    "role": user.role,
                    "api_spec": api_spec,
                }

    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
