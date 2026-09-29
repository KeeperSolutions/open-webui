"""merge billing_alert_state and connector_connection heads

Revision ID: 8dbd041fb2fd
Revises: 846572c729df, 9c2b6f1e4a7d
Create Date: 2026-09-22 10:46:21.030628

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db


# revision identifiers, used by Alembic.
revision: str = '8dbd041fb2fd'
down_revision: Union[str, None] = ('846572c729df', '9c2b6f1e4a7d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
