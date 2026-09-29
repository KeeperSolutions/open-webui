"""merge team pii groups head with billing alert state head

Revision ID: e0743c4f0736
Revises: dd89202fd4f6, fb9021bdf183
Create Date: 2026-09-29 16:13:20.452733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db


# revision identifiers, used by Alembic.
revision: str = 'e0743c4f0736'
down_revision: Union[str, None] = ('dd89202fd4f6', 'fb9021bdf183')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
