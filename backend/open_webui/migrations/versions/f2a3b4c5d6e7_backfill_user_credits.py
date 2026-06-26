"""Backfill user_credits for existing stripe_billing rows that have no credit record.

For every stripe_billing row whose plan_tier is in (trial, pro, premium) and that
has no corresponding user_credits row, insert a row with:
  - balance = PLAN_CREDITS[plan_tier]   (using the current CREDITS_PER_EUR_CENT rate)
  - credits_per_eur_cent = current global rate

This is idempotent — rows that already exist are skipped via INSERT OR IGNORE / ON CONFLICT.

Revision ID: f2a3b4c5d6e7
Revises: e5f6a7b8c9d0
Create Date: 2026-06-25
"""

import os
import time
import uuid
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, tuple, None] = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

_CREDITS_TIERS = {"trial", "pro", "premium"}


def _get_rate() -> float:
    val = os.environ.get("CREDITS_PER_EUR_CENT", "1.82")
    try:
        return float(val)
    except (TypeError, ValueError):
        return 1.82


def _plan_balance(plan_tier: str, rate: float) -> int:
    if plan_tier == "trial":
        return round(2.00 * 100 * rate)
    if plan_tier == "pro":
        return 1300
    if plan_tier == "premium":
        return 3800
    return 0


def upgrade() -> None:
    conn = op.get_bind()
    rate = _get_rate()
    now = int(time.time())

    # Fetch all stripe_billing rows that need a user_credits row.
    # LEFT JOIN so we only touch users with no existing record.
    rows = conn.execute(
        sa.text(
            """
            SELECT u.email, sb.plan_tier
            FROM stripe_billing sb
            JOIN "user" u ON u.id = sb.user_id
            LEFT JOIN user_credits uc ON uc.user_id = u.email
            WHERE sb.plan_tier IN ('trial', 'pro', 'premium')
              AND uc.user_id IS NULL
            """
        )
    ).fetchall()

    for email, plan_tier in rows:
        balance = _plan_balance(plan_tier, rate)
        conn.execute(
            sa.text(
                """
                INSERT INTO user_credits (id, user_id, balance, credits_per_eur_cent, updated_at)
                VALUES (:id, :user_id, :balance, :rate, :updated_at)
                ON CONFLICT (user_id) DO NOTHING
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": email,
                "balance": balance,
                "rate": rate,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    # Remove only rows that were inserted by this migration (those created at the same
    # second as a batch is indistinguishable, so downgrade is a no-op to avoid data loss).
    pass
