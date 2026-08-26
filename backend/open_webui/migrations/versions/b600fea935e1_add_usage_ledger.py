"""Add usage_ledger table for precise EUR billing

Revision ID: b600fea935e1
Revises: 6927529b47e2
Create Date: 2026-06-20 00:00:00.000000

Stores individual Langfuse LLM observations with the ECB EUR/USD rate at the
time of the call. Billing cost functions read from this table instead of calling
Langfuse on every request, ensuring consistent EUR values across all UI surfaces.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b600fea935e1"
down_revision: Union[str, None] = "6927529b47e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("langfuse_observation_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("eur_usd_rate", sa.Float(), nullable=True),
        sa.Column("cost_eur", sa.Float(), nullable=True),
        sa.Column("observed_at", sa.BigInteger(), nullable=False),
        sa.Column("synced_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("langfuse_observation_id", name="uq_usage_ledger_observation_id"),
    )
    op.create_index("ix_usage_ledger_user_id", "usage_ledger", ["user_id"])
    op.create_index("ix_usage_ledger_observed_at", "usage_ledger", ["observed_at"])


def downgrade():
    op.drop_index("ix_usage_ledger_observed_at", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_user_id", table_name="usage_ledger")
    op.drop_table("usage_ledger")
