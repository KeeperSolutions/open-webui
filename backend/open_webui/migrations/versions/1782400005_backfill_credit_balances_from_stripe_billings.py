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


def upgrade():
    # Backfill credit_balances for existing paid users/teams
    op.execute("""
        WITH plan_credits AS (
            SELECT 'pro' AS plan_tier, 1300 AS credits UNION ALL
            SELECT 'premium', 3800 UNION ALL
            SELECT 'team', 10000
        ),
        to_migrate AS (
            SELECT 
                sb.id AS billing_id,
                CASE 
                    WHEN sb.plan_tier IN ('pro', 'premium') THEN 'user'
                    WHEN sb.plan_tier = 'team' THEN 'team'
                    ELSE 'user'
                END AS owner_type,
                COALESCE(sb.team_id, sb.user_id) AS owner_id,
                COALESCE(pc.credits, 1300) AS subscription_credits,
                1.82::float AS credits_per_eur_cent,
                EXTRACT(EPOCH FROM NOW())::bigint AS now_ts
            FROM stripe_billing sb
            LEFT JOIN plan_credits pc ON pc.plan_tier = sb.plan_tier
            WHERE sb.plan_tier IN ('pro', 'premium', 'team')
              AND (sb.stripe_subscription_status = 'active' OR sb.stripe_subscription_status IS NULL)
        )
        INSERT INTO credit_balances (
            id, owner_type, owner_id, subscription_credits, topup_credits, 
            credits_per_eur_cent, period_start, updated_at
        )
        SELECT 
            gen_random_uuid()::text,
            tm.owner_type,
            tm.owner_id,
            tm.subscription_credits,
            0,
            tm.credits_per_eur_cent,
            tm.now_ts,
            tm.now_ts
        FROM to_migrate tm
        LEFT JOIN credit_balances cb 
            ON cb.owner_type = tm.owner_type 
           AND cb.owner_id = tm.owner_id
        WHERE cb.id IS NULL;
    """)


def downgrade():
    # This is a data-only migration. We do not delete existing credit_balances on downgrade.
    pass
