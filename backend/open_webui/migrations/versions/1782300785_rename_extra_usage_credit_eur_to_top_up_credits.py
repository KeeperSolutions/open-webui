"""Rename extra_usage_credit_eur to top_up_credits on teams (convert values)

Revision ID: 1782300785
Revises: 1782300784
Create Date: 2026-06-24 11:33:05.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782300785"
down_revision: Union[str, None] = "1782300784"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Add new column
    op.add_column(
        "teams",
        sa.Column("top_up_credits", sa.Integer(), nullable=False, server_default="0"),
    )

    # Convert existing values: 1000 credits = 6 EUR => credits = round(eur * 1000 / 6)
    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT id, extra_usage_credit_eur FROM teams"))
    for row in result:
        eur = row[1] or 0.0
        credits = max(0, round(eur * 1000 / 6))
        connection.execute(
            sa.text("UPDATE teams SET top_up_credits = :c WHERE id = :id"),
            {"c": credits, "id": row[0]},
        )

    # Drop old column
    op.drop_column("teams", "extra_usage_credit_eur")


def downgrade():
    op.add_column(
        "teams",
        sa.Column("extra_usage_credit_eur", sa.Float(), nullable=False, server_default="0.0"),
    )

    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT id, top_up_credits FROM teams"))
    for row in result:
        credits = row[1] or 0
        eur = round(credits * 6 / 1000, 2)
        connection.execute(
            sa.text("UPDATE teams SET extra_usage_credit_eur = :e WHERE id = :id"),
            {"e": max(0.0, eur), "id": row[0]},
        )

    op.drop_column("teams", "top_up_credits")
