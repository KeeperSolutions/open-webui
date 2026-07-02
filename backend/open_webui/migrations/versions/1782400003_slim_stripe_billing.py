"""Slim down stripe_billing — remove top_up_credits, checkout_session_id, topup_checkout_session_id

Revision ID: 1782400003
Revises: 1782400002
Create Date: 2026-07-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782400003"
down_revision: Union[str, None] = "1782400002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # SQLite requires batch mode to drop columns
    with op.batch_alter_table("stripe_billing") as batch_op:
        batch_op.drop_column("top_up_credits")
        batch_op.drop_column("checkout_session_id")
        batch_op.drop_column("topup_checkout_session_id")


def downgrade():
    with op.batch_alter_table("stripe_billing") as batch_op:
        batch_op.add_column(sa.Column("top_up_credits", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("checkout_session_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("topup_checkout_session_id", sa.Text(), nullable=True))
