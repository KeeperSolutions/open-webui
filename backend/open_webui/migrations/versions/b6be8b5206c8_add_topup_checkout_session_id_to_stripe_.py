"""add topup_checkout_session_id to stripe_billing

Revision ID: b6be8b5206c8
Revises: 1782300785
Create Date: 2026-06-25 14:28:08.498374

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db


# revision identifiers, used by Alembic.
revision: str = 'b6be8b5206c8'
down_revision: Union[str, None] = '1782300785'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stripe_billing', sa.Column('topup_checkout_session_id', sa.Text(), nullable=True))
    op.add_column('stripe_billing', sa.Column('top_up_credits', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('stripe_billing', 'topup_checkout_session_id')
    op.drop_column('stripe_billing', 'top_up_credits')
