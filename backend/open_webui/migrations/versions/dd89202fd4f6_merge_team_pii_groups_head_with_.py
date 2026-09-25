"""merge team pii groups head with connector connection head

Revision ID: dd89202fd4f6
Revises: 9c2b6f1e4a7d, b6d1a4f0c7e2
Create Date: 2026-09-22 15:40:13.661191

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db


# revision identifiers, used by Alembic.
revision: str = 'dd89202fd4f6'
down_revision: Union[str, None] = ('9c2b6f1e4a7d', 'b6d1a4f0c7e2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
