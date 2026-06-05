"""Add team billing tables and team_id to stripe_billing

Revision ID: d8d905b57f4e
Revises: f1a2b3c4d5e7
Create Date: 2026-05-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8d905b57f4e"
down_revision: Union[str, None] = "f1a2b3c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "teams",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True, unique=True),
        sa.Column("stripe_subscription_id", sa.Text(), nullable=True, unique=True),
        sa.Column("stripe_subscription_item_id", sa.Text(), nullable=True),
        sa.Column("stripe_payment_method_id", sa.Text(), nullable=True),
        sa.Column("subscription_status", sa.Text(), nullable=True),
        sa.Column("seat_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("checkout_session_id", sa.Text(), nullable=True),
        sa.Column("topup_checkout_session_id", sa.Text(), nullable=True),
        sa.Column("monthly_usage_budget_eur", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("extra_usage_credit_eur", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    op.create_table(
        "team_members",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="member"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.create_index("uq_team_members_user_id", "team_members", ["user_id"], unique=True)
    op.create_index("uq_team_members_team_user", "team_members", ["team_id", "user_id"], unique=True)

    op.create_table(
        "team_invites",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("invited_email", sa.Text(), nullable=False),
        sa.Column("invited_by", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_team_invites_team_id", "team_invites", ["team_id"])
    op.create_index("ix_team_invites_token", "team_invites", ["token"])

    op.add_column("stripe_billing", sa.Column("team_id", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("stripe_billing", "team_id")
    op.drop_index("uq_team_members_team_user", "team_members")
    op.drop_index("uq_team_members_user_id", "team_members")
    op.drop_table("team_invites")
    op.drop_table("team_members")
    op.drop_table("teams")
