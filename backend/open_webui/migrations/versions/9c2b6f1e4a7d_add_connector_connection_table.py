"""Add connector_connection table

Revision ID: 9c2b6f1e4a7d
Revises: 71cd9d447074
Create Date: 2026-09-08 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9c2b6f1e4a7d'
down_revision: Union[str, None] = '71cd9d447074'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    if 'connector_connection' not in existing_tables:
        op.create_table(
            'connector_connection',
            sa.Column('id', sa.Text(), primary_key=True, nullable=False, unique=True),
            sa.Column(
                'user_id',
                sa.Text(),
                sa.ForeignKey('user.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('connector', sa.Text(), nullable=False),
            sa.Column('external_account', sa.Text(), nullable=True),
            sa.Column('token', sa.Text(), nullable=False),
            sa.Column('scopes', sa.Text(), nullable=True),
            sa.Column('expires_at', sa.BigInteger(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            sa.UniqueConstraint('user_id', 'connector', name='uq_connector_connection_user_connector'),
        )

    existing_indexes = (
        {idx['name'] for idx in inspector.get_indexes('connector_connection')}
        if 'connector_connection' in existing_tables
        else set()
    )

    if 'idx_connector_connection_user_id' not in existing_indexes:
        op.create_index('idx_connector_connection_user_id', 'connector_connection', ['user_id'])


def downgrade() -> None:
    op.drop_index('idx_connector_connection_user_id', table_name='connector_connection')
    op.drop_table('connector_connection')
