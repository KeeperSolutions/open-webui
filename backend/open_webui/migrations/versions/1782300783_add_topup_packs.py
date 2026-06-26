"""Add topup_packs table

Revision ID: 1782300783
Revises: a7b8c9d0e1f2
Create Date: 2026-06-24 11:33:03.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782300783"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "topup_packs",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("price_eur", sa.Float(), nullable=False),
        sa.Column("stripe_price_id", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table("topup_packs")