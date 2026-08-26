"""merge scim and billing heads

Revision ID: 81de4454640d
Revises: b2c3d4e5f6a7, 1782400005
Create Date: 2026-07-21 00:00:00.000000

Joins upstream's Skills/access-grant/SCIM chain (terminating at
b2c3d4e5f6a7_add_scim_column_to_user_table) with this fork's billing/
credits/ledger chain (terminating at 1782400005_backfill_credit_balances_
from_stripe_billings) into a single head. These diverged only because two
independent chains happened to pick colliding revision IDs upstream
(a1b2c3d4e5f6, b2c3d4e5f6a7) that this fork's own migrations also picked —
see 6927529b47e2_add_is_encrypted_to_chat.py and b600fea935e1_add_usage_
ledger.py, which were renamed off those same IDs to resolve the collision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '81de4454640d'
down_revision: Union[str, tuple, None] = ('b2c3d4e5f6a7', '1782400005')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
