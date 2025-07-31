"""add confidios tables

Revision ID: 20250731135017
Revises: 9f0c9cd09105
Create Date: 2025-07-31 12:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db

# revision identifiers, used by Alembic.
revision: str = "20250731135017"
down_revision: Union[str, None] = "9f0c9cd09105"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create confidios_session table
    op.create_table(
        "confidios_session",
        sa.Column("user_id", sa.VARCHAR(255), nullable=False, primary_key=True),
        sa.Column("confidios_user", sa.VARCHAR(255), nullable=True),
        sa.Column("confidios_session_id", sa.VARCHAR(255), nullable=True),
        sa.Column("balance", sa.VARCHAR(255), nullable=True),
        sa.Column("is_logged_in", sa.Boolean(), nullable=True, default=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )

    # Create confidios_user table
    op.create_table(
        "confidios_user",
        sa.Column("user_id", sa.VARCHAR(255), nullable=False, primary_key=True),
        sa.Column("confidios_username", sa.VARCHAR(255), nullable=False, unique=True),
        sa.Column("confidios_session_id", sa.VARCHAR(255), nullable=True),
        sa.Column("balance", sa.VARCHAR(255), nullable=False),
        sa.Column("is_session_active", sa.Boolean(), nullable=True, default=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table("confidios_user")
    op.drop_table("confidios_session")
