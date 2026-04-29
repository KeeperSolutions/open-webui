"""Add plan_tier and checkout_session_id to stripe_billing

Revision ID: f1a2b3c4d5e7
Revises: e1a2b3c4d5e6
Create Date: 2026-04-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e7"
down_revision: Union[str, None] = "e1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("stripe_billing", sa.Column("plan_tier", sa.Text(), nullable=True))
    op.add_column("stripe_billing", sa.Column("checkout_session_id", sa.Text(), nullable=True))

    # Mark all pre-existing rows as internal (existing users are all internal)
    op.execute("UPDATE stripe_billing SET plan_tier = 'internal' WHERE plan_tier IS NULL")


def downgrade():
    op.drop_column("stripe_billing", "checkout_session_id")
    op.drop_column("stripe_billing", "plan_tier")
