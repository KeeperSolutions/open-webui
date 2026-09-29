"""Add claimed_at to billing_alert_state

Revision ID: fb9021bdf183
Revises: 8dbd041fb2fd
Create Date: 2026-09-22 00:00:00.000000

Supports a short-TTL in-flight claim state (status=claiming/recovering) separate from the
alert cooldown, so a claim orphaned by a process crash/restart between the claim write and
the email send (no exception runs, so release_claim() never fires) becomes reclaimable after
BILLING_ALERT_CLAIM_TTL_SECONDS instead of blocking a real alert for the full
BILLING_ALERT_COOLDOWN_SECONDS. See models/billing_alert_state.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = 'fb9021bdf183'
down_revision: Union[str, None] = '8dbd041fb2fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing_columns = {col['name'] for col in inspect(conn).get_columns('billing_alert_state')}
    if 'claimed_at' not in existing_columns:
        with op.batch_alter_table('billing_alert_state') as batch_op:
            batch_op.add_column(sa.Column('claimed_at', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('billing_alert_state') as batch_op:
        batch_op.drop_column('claimed_at')
