"""Add model_class table

Revision ID: a7b8c9d0e1f2
Revises: b2c3d4e5f6a7
Create Date: 2026-06-22 00:00:00.000000

Add order column for explicit sorting.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "model_class",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("models", sa.JSON(), nullable=True),
        sa.Column("credit_burn", sa.Float(), nullable=False),
        sa.Column("msgs_pro", sa.Text(), nullable=True),
        sa.Column("msgs_premium", sa.Text(), nullable=True),
        sa.Column("msgs_business", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, unique=True),
    )


def downgrade():
    op.drop_table("model_class")
