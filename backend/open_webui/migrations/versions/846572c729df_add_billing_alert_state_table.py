"""Add billing_alert_state table

Revision ID: 846572c729df
Revises: 71cd9d447074
Create Date: 2026-09-21 00:00:00.000000

Persists admin billing-alert dedup state (unpriced model / pricing recovered /
ECB unreachable) so repeat alerts are suppressed across process restarts and
across multiple concurrent instances, not just within one process's memory.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from open_webui.migrations.util import get_existing_tables

revision: str = '846572c729df'
down_revision: Union[str, None] = '71cd9d447074'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    if 'billing_alert_state' not in existing_tables:
        op.create_table(
            'billing_alert_state',
            sa.Column('id', sa.Text(), primary_key=True, nullable=False, unique=True),
            sa.Column('alert_type', sa.Text(), nullable=False),
            sa.Column('key', sa.Text(), nullable=False),
            sa.Column('status', sa.Text(), nullable=False),
            sa.Column('last_alerted_at', sa.BigInteger(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.UniqueConstraint('alert_type', 'key', name='uq_billing_alert_state_type_key'),
        )


def downgrade() -> None:
    op.drop_table('billing_alert_state')
