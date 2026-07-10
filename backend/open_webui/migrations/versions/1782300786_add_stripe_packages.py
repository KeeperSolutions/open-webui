"""Add stripe_packages table

Revision ID: 1782300786
Revises: 29e00028e075
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782300786"
down_revision: Union[str, None] = "29e00028e075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "stripe_packages",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("plan_tier", sa.Text(), nullable=False),
        sa.Column("stripe_price_id", sa.Text(), nullable=False, unique=True),
        sa.Column("price_eur", sa.Float(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("seat_count", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table("stripe_packages")
