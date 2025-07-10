import sqlite3
from datetime import datetime
from typing import Optional


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
                "is_session_active": bool(row["is_session_active"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        return None


async def update_confidios_user_session(user_id: str, session_data: dict):
    """Update user's session data in confidios_user table"""
    timestamp = int(datetime.now().timestamp())

    try:
        confidios_username = session_data.get("u")
        balance = session_data.get("balance")
        session_id = session_data.get("sid")

        with sqlite3.connect("data/webui.db") as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                UPDATE confidios_user
                SET confidios_session_id = ?, balance = ?, is_session_active = ?, updated_at = ?
                WHERE user_id = ?
            """,
                (session_id, balance, session_id is not None, timestamp, user_id),
            )

            conn.commit()

    except Exception as e:
        print(f"Session update error: {e}")
        raise


async def clear_confidios_user_session(user_id: str):
    """Clear user's session data in confidios_user table"""
    timestamp = int(datetime.now().timestamp())

    try:
        with sqlite3.connect("data/webui.db") as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                UPDATE confidios_user
                SET confidios_session_id = NULL, is_session_active = FALSE, updated_at = ?
                WHERE user_id = ?
            """,
                (timestamp, user_id),
            )

            conn.commit()

    except Exception as e:
        print(f"Session clear error: {e}")
        raise


async def save_confidios_user(user_id: str, confidios_data: dict):
    """Save Confidios user data to database (for user creation)"""
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
                    session_id is not None,
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()

    except Exception as e:
        print(f"Database save error: {e}")
        raise


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
