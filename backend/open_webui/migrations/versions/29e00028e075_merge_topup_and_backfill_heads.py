"""merge topup and backfill heads

Revision ID: 29e00028e075
Revises: b6be8b5206c8, f2a3b4c5d6e7
Create Date: 2026-06-26 15:20:40.736105

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db


# revision identifiers, used by Alembic.
revision: str = '29e00028e075'
down_revision: Union[str, None] = ('b6be8b5206c8', 'f2a3b4c5d6e7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
