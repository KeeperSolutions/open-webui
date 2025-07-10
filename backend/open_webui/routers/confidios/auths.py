import os
from typing import Dict, Optional
import sqlite3
import json
from datetime import datetime

import aiohttp
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel

from .confidios_db import (
    get_confidios_user,
    update_confidios_user_session,
    clear_confidios_user_session,
)

load_dotenv()
router = APIRouter()

CONFIDIOS_BASE_URL = os.getenv("CONFIDIOS_BASE_URL")
CONFIDIOS_ADMIN_IDENTITY = os.getenv("CONFIDIOS_ADMIN_IDENTITY")
CONFIDIOS_ADMIN_PASSWORD = os.getenv("CONFIDIOS_ADMIN_PASSWORD")
CONFIDIOS_USER_PASSWORD = os.getenv(
    "CONFIDIOS_USER_PASSWORD", "test-password"
)  # Set this in .env

# In-memory cache for performance (optional, but recommended)
confidios_sessions: Dict[str, dict] = {}


# Add response model
class ConfidiosLoginResponse(BaseModel):
    balance: str
    sid: str
    u: str


# Add request model
class UserLoginRequest(BaseModel):
    confidios_username: str


# Database helper functions
async def save_confidios_session(user_id: str, session_data: dict):
    """Save Confidios session to database"""
    timestamp = int(datetime.now().timestamp())

    # Update this path to match your database location
    with sqlite3.connect("data/webui.db") as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO confidios_session
            (user_id, confidios_user, confidios_session_id, balance, is_logged_in, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                user_id,
                session_data["confidios_user"],
                session_data["confidios_session_id"],
                session_data["balance"],
                session_data["is_logged_in"],
                timestamp,
                timestamp,
            ),
        )
        conn.commit()


async def get_confidios_session(user_id: str) -> Optional[dict]:
    """Get Confidios session from database"""
    with sqlite3.connect("data/webui.db") as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM confidios_session WHERE user_id = ? AND is_logged_in = 1",
            (user_id,),
        )
        row = cursor.fetchone()

        if row:
            return {
                "confidios_user": row["confidios_user"],
                "confidios_session_id": row["confidios_session_id"],
                "balance": row["balance"],
                "is_logged_in": bool(row["is_logged_in"]),
            }
        return None


async def update_confidios_session_logout(user_id: str):
    """Mark user as logged out in database"""
    timestamp = int(datetime.now().timestamp())

    with sqlite3.connect("data/webui.db") as conn:
        conn.execute(
            """
            UPDATE confidios_session
            SET confidios_user = NULL,
                confidios_session_id = NULL,
                balance = NULL,
                is_logged_in = 0,
                updated_at = ?
            WHERE user_id = ?
        """,
            (timestamp, user_id),
        )
        conn.commit()


async def delete_confidios_session(user_id: str):
    """Delete Confidios session from database"""
    with sqlite3.connect("data/webui.db") as conn:
        conn.execute("DELETE FROM confidios_session WHERE user_id = ?", (user_id,))
        conn.commit()


async def update_confidios_user_session(user_id: str, session_data: dict):
    """Update user's session data in confidios_user table"""
    timestamp = int(datetime.now().timestamp())

    try:
        confidios_username = session_data.get("u")
        balance = session_data.get("balance")
        session_id = session_data.get("sid")

        print(f"🔧 update_confidios_user_session called:")
        print(f"   - user_id: {user_id}")
        print(f"   - confidios_username: {confidios_username}")
        print(f"   - balance: {balance}")
        print(f"   - session_id: {session_id}")
        print(f"   - timestamp: {timestamp}")

        with sqlite3.connect("data/webui.db") as conn:
            cursor = conn.cursor()

            # Debug: Check current state before update
            cursor.execute("SELECT * FROM confidios_user WHERE user_id = ?", (user_id,))
            before_update = cursor.fetchone()
            print(f"📊 Before update: {before_update}")

            # Perform the update
            cursor.execute(
                """
                UPDATE confidios_user
                SET confidios_session_id = ?, balance = ?, is_session_active = ?, updated_at = ?
                WHERE user_id = ?
            """,
                (session_id, balance, session_id is not None, timestamp, user_id),
            )

            rows_affected = cursor.rowcount
            print(f"📝 Rows affected by update: {rows_affected}")

            conn.commit()

            # Debug: Check state after update
            cursor.execute("SELECT * FROM confidios_user WHERE user_id = ?", (user_id,))
            after_update = cursor.fetchone()
            print(f"📊 After update: {after_update}")

            print(f"✅ Session update completed for user {user_id}")

    except Exception as e:
        print(f"💥 Session update error: {e}")
        import traceback

        print(f"📚 Traceback: {traceback.format_exc()}")
        raise


@router.post("/auths/login")
async def confidios_admin_login(
    user=Depends(get_verified_user),
):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can access this endpoint",
        )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/login",
                json={
                    "identity": CONFIDIOS_ADMIN_IDENTITY,
                    "password": CONFIDIOS_ADMIN_PASSWORD,
                },
            ) as response:
                if response.status == 401:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid credentials for Confidios service",
                    )
                elif response.status == 403:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access denied by Confidios service",
                    )
                elif response.status != 200:
                    error_detail = "Failed to login to Confidios service"
                    try:
                        error_body = await response.json()
                        error_detail = f"{error_body.get('detail', error_detail)}"
                    except Exception:
                        error_detail = await response.text()

                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
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

                # Save session data
                session_data = {
                    "confidios_user": confidios_resp.u,
                    "confidios_session_id": confidios_resp.sid,
                    "balance": confidios_resp.balance,
                    "is_logged_in": True,
                }

                # Save to both memory cache and database
                confidios_sessions[user.id] = session_data
                await save_confidios_session(user.id, session_data)

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


@router.post("/auths/logout")
async def confidios_admin_logout(
    user=Depends(get_verified_user),
):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can access this endpoint",
        )

    # Check if user has active Confidios session (memory first, then database)
    session_data = None
    if user.id in confidios_sessions:
        session_data = confidios_sessions[user.id]
    else:
        session_data = await get_confidios_session(user.id)
        if not session_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No active Confidios session found",
            )

    # Get session data for logout request
    session_header = {
        "u": session_data["confidios_user"],
        "sid": session_data["confidios_session_id"],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/logout",
                headers={
                    "X-Confidios-Session-Id": json.dumps(session_header),
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status != 200:
                    error_detail = "Failed to logout from Confidios service"
                    try:
                        error_body = await response.json()
                        error_detail = f"{error_body.get('detail', error_detail)}"
                    except Exception:
                        error_detail = await response.text()

                    raise HTTPException(
                        status_code=response.status,
                        detail=error_detail,
                    )

                # Successfully logged out from Confidios, update both memory and database
                logout_data = {
                    "confidios_user": None,
                    "confidios_session_id": None,
                    "balance": None,
                    "is_logged_in": False,
                }

                confidios_sessions[user.id] = logout_data
                await update_confidios_session_logout(user.id)

                return {
                    "status": "Successfully logged out from Confidios service",
                    "user_id": user.id,
                    "confidios_is_logged_in": False,
                }

    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to logout: {str(e)}",
        )


@router.get("/status")
async def get_confidios_status(user=Depends(get_verified_user)):
    # Check memory cache first
    if user.id in confidios_sessions:
        session_data = confidios_sessions[user.id]
        if session_data["is_logged_in"]:
            return session_data

    # Check database if not in memory or not logged in
    session_data = await get_confidios_session(user.id)
    if session_data and session_data["is_logged_in"]:
        # Cache in memory for future requests
        confidios_sessions[user.id] = session_data
        return session_data

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No active Confidios session found. Please login first.",
    )


@router.post("/auths/user/login")
async def confidios_user_login(
    user=Depends(get_verified_user),
):
    """Login endpoint for regular users - matches your frontend call"""

    # Check if user exists in Confidios
    user_data = await get_confidios_user(user.id)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in Confidios. Please contact admin to create your account.",
        )

    # Use the confidios_username from database
    confidios_username = user_data["confidios_username"]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/login",
                json={
                    "identity": confidios_username,
                    "password": CONFIDIOS_USER_PASSWORD,
                },
            ) as response:
                if response.status == 401:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid credentials for Confidios service",
                    )
                elif response.status == 403:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access denied by Confidios service",
                    )
                elif response.status != 200:
                    error_detail = "Failed to login to Confidios service"
                    try:
                        error_body = await response.json()
                        error_detail = f"{error_body.get('detail', error_detail)}"
                    except Exception:
                        error_detail = await response.text()

                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=error_detail,
                    )

                resp = await response.json()

                # Validate response structure
                confidios_resp = ConfidiosLoginResponse(**resp)

                # Update session data in confidios_user table
                await update_confidios_user_session(user.id, resp)

                return {
                    "status": "Successfully logged in to Confidios service",
                    "user_id": user.id,
                    "confidios_user": confidios_resp.u,
                    "confidios_balance": confidios_resp.balance,
                    "confidios_is_logged_in": True,
                }

    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
    except Exception as e:
        print(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to login: {str(e)}",
        )


@router.post("/auths/user/logout")
async def confidios_user_logout(
    user=Depends(get_verified_user),
):
    """Logout endpoint for regular users - always clears local session"""

    try:
        # Get user data first
        user_data = await get_confidios_user(user.id)
        if not user_data:
            return {
                "status": "User not found, assuming already logged out",
                "user_id": user.id,
                "confidios_is_logged_in": False,
            }

        # ALWAYS clear the local session first
        await clear_confidios_user_session(user.id)

        # Then try to notify Confidios API (but don't fail if this doesn't work)
        if user_data.get("confidios_session_id") and user_data.get("is_session_active"):
            session_header = {
                "u": user_data["confidios_username"],
                "sid": user_data["confidios_session_id"],
            }

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{CONFIDIOS_BASE_URL}/logout",
                        headers={
                            "X-Confidios-Session-Id": json.dumps(session_header),
                            "Content-Type": "application/json",
                        },
                        timeout=aiohttp.ClientTimeout(total=5),  # Short timeout
                    ) as response:
                        # API response logged only if there's an issue
                        if response.status != 200:
                            print(f"Confidios API logout returned {response.status}")

            except Exception as api_error:
                print(f"Confidios API logout failed: {api_error}")

        return {
            "status": "Successfully logged out",
            "user_id": user.id,
            "confidios_is_logged_in": False,
        }

    except Exception as e:
        print(f"Error during logout: {e}")

        # Even if there's an error, try to clear the session
        try:
            await clear_confidios_user_session(user.id)
        except Exception as clear_error:
            print(f"Failed to clear session: {clear_error}")

        # Don't raise an HTTP exception - just return success
        # The important thing is that we tried to clear the local session
        return {
            "status": "Logout completed (with errors)",
            "user_id": user.id,
            "confidios_is_logged_in": False,
        }
