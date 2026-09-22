"""merge v0.11.0 email-index head with fork head

Revision ID: 71cd9d447074
Revises: ce5cc6fe333b, f0bd01a18a3d
Create Date: 2026-09-03 15:43:46.961280

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db


# revision identifiers, used by Alembic.
revision: str = '71cd9d447074'
down_revision: Union[str, None] = ('ce5cc6fe333b', 'f0bd01a18a3d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
