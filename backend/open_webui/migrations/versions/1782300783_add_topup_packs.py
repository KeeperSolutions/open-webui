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

    # Seed the 7 top-up packs (test mode prices)
    op.bulk_insert(
        sa.table(
            "topup_packs",
            sa.column("id", sa.Text),
            sa.column("credits", sa.Integer),
            sa.column("price_eur", sa.Float),
            sa.column("stripe_price_id", sa.Text),
            sa.column("created_at", sa.BigInteger),
        ),
        [
            {"id": "pack_25",   "credits": 25,   "price_eur": 5,   "stripe_price_id": "price_1Tlpk5HM5MSzOp4WtgaUKBZT", "created_at": 1782303441},
            {"id": "pack_50",   "credits": 50,   "price_eur": 10,  "stripe_price_id": "price_1Tlpk6HM5MSzOp4WhrWzfWjV", "created_at": 1782303442},
            {"id": "pack_100",  "credits": 100,  "price_eur": 20,  "stripe_price_id": "price_1Tlpk6HM5MSzOp4WL4U7MfPr", "created_at": 1782303442},
            {"id": "pack_250",  "credits": 250,  "price_eur": 50,  "stripe_price_id": "price_1Tlpk7HM5MSzOp4WfhVewCmu", "created_at": 1782303443},
            {"id": "pack_500",  "credits": 500,  "price_eur": 100, "stripe_price_id": "price_1Tlpk8HM5MSzOp4WtBlwTANq", "created_at": 1782303444},
            {"id": "pack_1000", "credits": 1000, "price_eur": 200, "stripe_price_id": "price_1Tlpk8HM5MSzOp4WyvnvqS2a", "created_at": 1782303444},
            {"id": "pack_2000", "credits": 2000, "price_eur": 400, "stripe_price_id": "price_1Tlpk9HM5MSzOp4WV0ssSnxv", "created_at": 1782303445},
        ],
    )


def downgrade():
    op.drop_table("topup_packs")