"""Add stripe_purchase_history table

Revision ID: 1782400002
Revises: 1782400001
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782400002"
down_revision: Union[str, None] = "1782400001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "stripe_purchase_history",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=True),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True),
        sa.Column("stripe_subscription_id", sa.Text(), nullable=True),
        # Used for idempotency — unique per checkout session or invoice
        sa.Column("stripe_checkout_session_id", sa.Text(), nullable=True, unique=True),
        sa.Column("stripe_invoice_id", sa.Text(), nullable=True, unique=True),
        # event_type: subscription_start | renewal | topup | cancellation
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("plan_tier", sa.Text(), nullable=True),
        sa.Column("package_id", sa.Text(), nullable=True),  # FK → stripe_packages.id
        sa.Column("subscription_credits_granted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("topup_credits_granted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_eur", sa.Float(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_stripe_purchase_history_user_id",
        "stripe_purchase_history",
        ["user_id"],
    )
    op.create_index(
        "ix_stripe_purchase_history_team_id",
        "stripe_purchase_history",
        ["team_id"],
    )


def downgrade():
    op.drop_index("ix_stripe_purchase_history_team_id", "stripe_purchase_history")
    op.drop_index("ix_stripe_purchase_history_user_id", "stripe_purchase_history")
    op.drop_table("stripe_purchase_history")
