from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from open_webui.utils.auth import get_verified_user
from .auths import confidios_sessions, CONFIDIOS_BASE_URL
import aiohttp
import json
import re
import sqlite3
from datetime import datetime
from typing import Optional

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


async def save_confidios_user(user_id: str, confidios_data: dict):
    """Save Confidios user data to database"""
    print(f"Attempting to save user data: {confidios_data}")

    timestamp = int(datetime.now().timestamp())

    try:
        # Extract values with error checking
        confidios_username = confidios_data.get("u")
        balance = confidios_data.get("balance")
        session_id = confidios_data.get("sid")  # Could be None

        if not confidios_username:
            raise ValueError(
                f"Missing 'u' field in response. Available keys: {list(confidios_data.keys())}"
            )
        if not balance:
            raise ValueError(
                f"Missing 'balance' field in response. Available keys: {list(confidios_data.keys())}"
            )

        print(
            f"Extracted values - u: {confidios_username}, balance: {balance}, sid: {session_id or 'None'}"
        )

        with sqlite3.connect("data/webui.db") as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO confidios_user
                (user_id, confidios_username, confidios_session_id, balance, is_session_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    user_id,
                    confidios_username,
                    session_id,
                    balance,
                    session_id is not None,  # True if they have an active session
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
            print("Successfully saved user to database")

    except Exception as e:
        print(f"Database save error: {e}")
        raise


async def get_confidios_user(user_id: str) -> Optional[dict]:
    """Get Confidios user data from database"""
    with sqlite3.connect("data/webui.db") as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM confidios_user WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()

        if row:
            return {
                "user_id": row["user_id"],
                "confidios_username": row["confidios_username"],
                "confidios_session_id": row["confidios_session_id"],
                "balance": row["balance"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        return None


async def update_confidios_user_balance(user_id: str, new_balance: str):
    """Update user's balance in database"""
    timestamp = int(datetime.now().timestamp())

    with sqlite3.connect("data/webui.db") as conn:
        conn.execute(
            """
            UPDATE confidios_user
            SET balance = ?, updated_at = ?
            WHERE user_id = ?
        """,
            (new_balance, timestamp, user_id),
        )
        conn.commit()


@router.post("/create")
async def create_confidios_user(
    user_data: UserCreateRequest, current_user=Depends(get_verified_user)
):
    print(f"Creating user for: {user_data.email}")

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

    # Check if user already exists in Confidios
    existing_user = await get_confidios_user(user_data.user_id)
    if existing_user:
        return {
            "message": "User already exists in Confidios",
            "confidios_user": existing_user,
        }

    try:
        identity = clean_username(user_data.email)
        print(f"Cleaned username: {identity}")
    except ValueError as e:
        print(f"Username cleaning error: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Get session data
    session_data = confidios_sessions[current_user.id]
    session_header = {
        "u": session_data["confidios_user"],
        "sid": session_data["confidios_session_id"],
    }
    print(f"Session header: {session_header}")

    try:
        async with aiohttp.ClientSession() as session:
            print(f"Making request to: {CONFIDIOS_BASE_URL}/creat/user")
            async with session.post(
                f"{CONFIDIOS_BASE_URL}/creat/user",
                headers={
                    "X-Confidios-Session-Id": json.dumps(session_header),
                    "Content-Type": "application/json",
                },
                json={
                    "identity": identity,
                    "password": "test-password",
                },
            ) as response:
                print(f"Response status: {response.status}")
                print(f"Response headers: {dict(response.headers)}")

                if response.status != 201:
                    error_detail = "Failed to create Confidios user"
                    try:
                        error_body = await response.json()
                        print(f"Error response body: {error_body}")
                        error_detail = f"{error_body.get('detail', error_detail)}"
                    except Exception:
                        error_text = await response.text()
                        print(f"Error response text: {error_text}")
                        error_detail = error_text

                    raise HTTPException(
                        status_code=response.status, detail=error_detail
                    )

                resp_data = await response.json()
                print(f"SUCCESS - Confidios API Response: {resp_data}")
                print(f"Response keys: {list(resp_data.keys())}")

                # Save to database
                await save_confidios_user(user_data.user_id, resp_data)

                return {
                    "message": "Confidios user created successfully",
                    "confidios_user": resp_data,
                }

    except aiohttp.ClientError as e:
        print(f"aiohttp.ClientError: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Confidios service: {str(e)}",
        )
    except Exception as e:
        print(f"General exception: {e}")
        print(f"Exception type: {type(e)}")
        import traceback

        print(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process user data: {str(e)}",
        )


@router.get("/list")
async def get_confidios_users(current_user=Depends(get_verified_user)):
    """Get all Confidios users with their balances"""
    # Check if user is admin
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can view Confidios users",
        )

    try:
        with sqlite3.connect("data/webui.db") as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT u.id, u.name, u.email, u.role, u.profile_image_url,
                       cu.confidios_username, cu.balance, cu.created_at, cu.updated_at
                FROM user u
                LEFT JOIN confidios_user cu ON u.id = cu.user_id
                ORDER BY u.name
            """)

            users = []
            for row in cursor.fetchall():
                user_data = {
                    "id": row["id"],
                    "name": row["name"],
                    "email": row["email"],
                    "role": row["role"],
                    "profile_image_url": row["profile_image_url"],
                    "confidios_username": row["confidios_username"],
                    "confidios_balance": row["balance"],
                    "confidios_created_at": row["created_at"],
                    "confidios_updated_at": row["updated_at"],
                }
                users.append(user_data)

            return {"users": users}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get users: {str(e)}",
        )


@router.get("/me")
async def get_my_confidios_status(current_user=Depends(get_verified_user)):
    """Get Confidios status for the currently logged in user"""
    try:
        with sqlite3.connect("data/webui.db") as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT cu.confidios_username, cu.balance, cu.created_at, cu.updated_at
                FROM confidios_user cu
                WHERE cu.user_id = ?
            """,
                (current_user.id,),
            )

            row = cursor.fetchone()

            if row:
                return {
                    "is_confidios_user": True,
                    "confidios_username": row["confidios_username"],
                    "balance": row["balance"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }

            return {"is_confidios_user": False}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Confidios status: {str(e)}",
        )
