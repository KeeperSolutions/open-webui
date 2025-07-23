from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from open_webui.utils.auth import get_verified_user
from .confidios_db import get_confidios_user
import aiohttp
import json
import os
from dotenv import load_dotenv

from typing import ClassVar

import logging

log = logging.getLogger(__name__)

load_dotenv()
router = APIRouter()

CONFIDIOS_BASE_URL = os.getenv("CONFIDIOS_BASE_URL")
CONFIDIOS_BASE_USER_FOLDER = os.getenv("CONFIDIOS_BASE_USER_FOLDER", "home/data")


# Add request model
class ListFilesRequest(BaseModel):
    path: str


@router.post("/ls")
async def list_files(request: ListFilesRequest, user=Depends(get_verified_user)):
    """List files from Confidios - requires active session"""
    log.info(f"List files endpoint called for path: {request.path}")
    try:
        # Get user's Confidios data to check session
        user_data = await get_confidios_user(user.id)
        if not user_data or not user_data.get("is_session_active"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No active Confidios session found. Please login first.",
            )

        # Prepare session header
        session_header = {
            "u": user_data["confidios_username"],
            "sid": user_data["confidios_session_id"],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/ls",
                headers={
                    "X-Confidios-Session-Id": json.dumps(session_header),
                    "Content-Type": "application/json",
                },
                json={"path": request.path},
            ) as response:
                if response.status != 200:
                    error_detail = "Failed to list files"
                    try:
                        error_body = await response.json()
                        error_detail = error_body.get("detail", error_detail)
                    except Exception:
                        error_detail = await response.text()

                    raise HTTPException(
                        status_code=response.status, detail=error_detail
                    )

                data = await response.json()
                return {"status": "success", "files": data.get("filelist", [])}

    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error listing files: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list files: {str(e)}",
        )


@router.post("/cat")
async def read_file(request: ListFilesRequest, user=Depends(get_verified_user)):
    """Read file content from Confidios - requires active session"""
    log.info(f"Read file endpoint called for path: {request.path}")
    try:
        # Get user's Confidios data to check session
        user_data = await get_confidios_user(user.id)
        if not user_data or not user_data.get("is_session_active"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No active Confidios session found. Please login first.",
            )

        # Prepare session header
        session_header = {
            "u": user_data["confidios_username"],
            "sid": user_data["confidios_session_id"],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/cat",
                headers={
                    "X-Confidios-Session-Id": json.dumps(session_header),
                    "Content-Type": "application/json",
                },
                json={"path": request.path},
            ) as response:
                if response.status != 200:
                    error_detail = "Failed to read file"
                    try:
                        error_body = await response.json()
                        error_detail = error_body.get("detail", error_detail)
                    except Exception:
                        error_detail = await response.text()

                    raise HTTPException(
                        status_code=response.status, detail=error_detail
                    )

                content_text = await response.text()
                content_obj = json.loads(content_text)  # Parse the JSON string

                return {
                    "status": "success",
                    "content": {
                        "balance": content_obj.get("balance"),
                        "data": content_obj.get("data"),
                    },
                }

    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse response content: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error reading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read file: {str(e)}",
        )


class MakeDirectoryRequest(BaseModel):
    FOLDER_NAME_MAX_LENGTH: ClassVar[int] = 24
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        # Check if path starts with base folder
        if not v.startswith(CONFIDIOS_BASE_USER_FOLDER):
            raise ValueError(f"Path must start with {CONFIDIOS_BASE_USER_FOLDER}")

        # Get the folder name (last part of the path)
        folder_name = v.split("/")[-1]

        # Check for spaces
        if " " in folder_name:
            raise ValueError("Folder name cannot contain spaces")

        # Check length
        if len(folder_name) > cls.FOLDER_NAME_MAX_LENGTH:
            raise ValueError(
                f"Folder name cannot exceed {cls.FOLDER_NAME_MAX_LENGTH} characters"
            )

        return v


@router.post("/mkdir", status_code=status.HTTP_201_CREATED)
async def make_directory(
    request: MakeDirectoryRequest, user=Depends(get_verified_user)
):
    """Create a new directory in Confidios - requires active session"""
    log.info(f"Make directory endpoint called for path: {request.path}")
    try:
        # Get user's Confidios data to check session
        user_data = await get_confidios_user(user.id)
        log.debug(f"User data retrieved: {json.dumps(user_data, default=str)}")

        if not user_data or not user_data.get("is_session_active"):
            log.warning(f"No active session found for user {user.id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No active Confidios session found. Please login first.",
            )

        # Prepare session header
        session_header = {
            "u": user_data["confidios_username"],
            "sid": user_data["confidios_session_id"],
        }
        log.debug(f"Prepared session header for request")

        async with aiohttp.ClientSession() as session:
            log.debug(f"Making POST request to {CONFIDIOS_BASE_URL}/mkdir")
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/mkdir",
                headers={
                    "X-Confidios-Session-Id": json.dumps(session_header),
                    "Content-Type": "application/json",
                },
                json={"path": request.path},
            ) as response:
                log.debug(f"Received response with status: {response.status}")

                if response.status != 201:
                    error_detail = "Failed to create directory"
                    try:
                        error_body = await response.json()
                        error_detail = error_body.get("detail", error_detail)
                        log.error(f"Error response body: {json.dumps(error_body)}")
                    except Exception as e:
                        error_detail = await response.text()
                        log.error(f"Failed to parse error response: {str(e)}")

                    log.error(
                        f"Directory creation failed with status {response.status}: {error_detail}"
                    )
                    raise HTTPException(
                        status_code=response.status, detail=error_detail
                    )

                content = await response.json()
                log.info(f"Directory created successfully at {request.path}")
                log.debug(f"Response content: {json.dumps(content)}")
                return {"balance": content.get("balance")}

    except aiohttp.ClientError as e:
        log.error(f"Client connection error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"Unexpected error while creating directory: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create directory: {str(e)}",
        )
