"""Add user_credits table for credits-based billing

Revision ID: c3d4e5f6a7b8
Revises: b600fea935e1
Create Date: 2026-06-24 00:00:00.000000

Stores per-user credit balance and the conversion rate locked at plan assignment.
Credits are the user-facing unit (whole numbers) derived from EUR cost.
credits_per_eur_cent is snapshotted from the global CREDITS_PER_EUR_CENT env var
at plan purchase time so a rate change only affects new signups/renewals.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b600fea935e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_credits",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_id", sa.Text, nullable=False, unique=True),
        sa.Column("balance", sa.Integer, nullable=False, server_default="0"),
        sa.Column("credits_per_eur_cent", sa.Float, nullable=False, server_default="1.82"),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
    )
    op.create_index("ix_user_credits_user_id", "user_credits", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_credits_user_id", table_name="user_credits")
    op.drop_table("user_credits")
