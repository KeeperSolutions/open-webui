"""Add top_up_credits to stripe_billing

Revision ID: 1782300784
Revises: 1782300783
Create Date: 2026-06-24 11:33:04.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782300784"
down_revision: Union[str, None] = "1782300783"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column(
        "stripe_billing",
        sa.Column("top_up_credits", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("stripe_billing", "top_up_credits")
