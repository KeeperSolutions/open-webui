"""Backfill credit_balances table from stripe_billings for existing paid users on staging.

This migration ensures that users/teams that were already on paid plans before the credit_balances table existed
get proper subscription_credits populated.

Revision ID: 1782400005
Revises: 1782400004
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
import uuid
import time

revision: str = "1782400005"
down_revision: Union[str, None] = "1782400004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PLAN_CREDITS = {"pro": 1300, "premium": 3800, "team": 10000}
CREDITS_PER_EUR_CENT = 1.82


def upgrade():
    # Backfill credit_balances for existing paid users/teams.
    # Done in Python (not raw dialect-specific SQL) so this runs on both
    # Postgres (staging/prod) and SQLite (local dev).
    bind = op.get_bind()

    rows = bind.execute(
        sa.text(
            """
            SELECT sb.id, sb.plan_tier, sb.team_id, sb.user_id, u.email AS user_email
            FROM stripe_billing sb
            LEFT JOIN "user" u ON u.id = sb.user_id
            WHERE sb.plan_tier IN ('pro', 'premium', 'team')
              AND (sb.subscription_status = 'active' OR sb.subscription_status IS NULL)
            """
        )
    ).fetchall()

    existing_owners = {
        (row.owner_type, row.owner_id)
        for row in bind.execute(
            sa.text("SELECT owner_type, owner_id FROM credit_balances")
        ).fetchall()
    }

    now_ts = int(time.time())
    credit_balances = sa.table(
        "credit_balances",
        sa.column("id"),
        sa.column("owner_type"),
        sa.column("owner_id"),
        sa.column("subscription_credits"),
        sa.column("topup_credits"),
        sa.column("credits_per_eur_cent"),
        sa.column("period_start"),
        sa.column("updated_at"),
    )

    to_insert = []
    for row in rows:
        owner_type = "team" if row.plan_tier == "team" else "user"
        # credit_balances keys "user" rows by email (see routers/billing.py),
        # not by stripe_billing.user_id.
        owner_id = row.team_id if owner_type == "team" else row.user_email
        if owner_id is None:
            continue
        if (owner_type, owner_id) in existing_owners:
            continue
        existing_owners.add((owner_type, owner_id))

        to_insert.append(
            {
                "id": str(uuid.uuid4()),
                "owner_type": owner_type,
                "owner_id": owner_id,
                "subscription_credits": PLAN_CREDITS.get(row.plan_tier, 1300),
                "topup_credits": 0,
                "credits_per_eur_cent": CREDITS_PER_EUR_CENT,
                "period_start": now_ts,
                "updated_at": now_ts,
            }
        )

    if to_insert:
        bind.execute(credit_balances.insert(), to_insert)


def downgrade():
    # This is a data-only migration. We do not delete existing credit_balances on downgrade.
    pass
