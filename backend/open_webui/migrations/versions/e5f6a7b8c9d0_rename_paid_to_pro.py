"""Rename plan_tier 'paid' to 'pro' in stripe_billings.

All individual subscribers were stored as 'paid' before tier canonicalisation.
We only have one paid tier today (pro), so the rename is safe and unambiguous.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-25
"""

from typing import Union
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, tuple, None] = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE stripe_billings SET plan_tier = 'pro' WHERE plan_tier = 'paid'")


def downgrade() -> None:
    op.execute("UPDATE stripe_billings SET plan_tier = 'paid' WHERE plan_tier = 'pro'")
