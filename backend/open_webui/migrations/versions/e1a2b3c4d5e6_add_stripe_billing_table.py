"""Add stripe_billing table

Revision ID: e1a2b3c4d5e6
Revises: 018012973d35
Create Date: 2026-04-08 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1a2b3c4d5e6"
down_revision: Union[str, None] = "c440947495f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "stripe_billing",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True),
        sa.Column("stripe_subscription_id", sa.Text(), nullable=True),
        sa.Column("stripe_subscription_item_id", sa.Text(), nullable=True),
        sa.Column("stripe_payment_method_id", sa.Text(), nullable=True),
        sa.Column("subscription_status", sa.Text(), nullable=True),
        sa.Column("free_tier_credit_applied", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_stripe_billing_user_id", "stripe_billing", ["user_id"], unique=True)
    op.create_index("ix_stripe_billing_customer_id", "stripe_billing", ["stripe_customer_id"], unique=True)


def downgrade():
    op.drop_index("ix_stripe_billing_customer_id", table_name="stripe_billing")
    op.drop_index("ix_stripe_billing_user_id", table_name="stripe_billing")
    op.drop_table("stripe_billing")
