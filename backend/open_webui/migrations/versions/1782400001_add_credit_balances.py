"""Add credit_balances table

Revision ID: 1782400001
Revises: 1782300786
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782400001"
down_revision: Union[str, None] = "1782300786"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "credit_balances",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("owner_type", sa.Text(), nullable=False),   # 'user' | 'team'
        sa.Column("owner_id", sa.Text(), nullable=False),     # user_id or team_id
        sa.Column("subscription_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("topup_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credits_per_eur_cent", sa.Float(), nullable=False, server_default="1.82"),
        sa.Column("period_start", sa.BigInteger(), nullable=True),  # Unix ts of current billing period
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("owner_type", "owner_id", name="uq_credit_balances_owner"),
    )


def downgrade():
    op.drop_table("credit_balances")
