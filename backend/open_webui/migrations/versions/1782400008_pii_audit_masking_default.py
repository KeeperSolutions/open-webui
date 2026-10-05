"""Allow PII audit rows that are not about a group, and record a preference value

Default and per-user preference changes have no group, so `group_id` becomes
nullable. `value` holds the preference written by a `preference_set` event.

Revision ID: 1782400008
Revises: e0743c4f0736
Create Date: 2026-10-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782400008"
down_revision: Union[str, None] = "e0743c4f0736"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode lets SQLite change a column's nullability.
    with op.batch_alter_table("pii_policy_audit") as batch:
        batch.alter_column("group_id", existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column("value", sa.Text(), nullable=True))


def downgrade() -> None:
    # Rows without a group cannot satisfy NOT NULL, so they are dropped.
    op.execute("DELETE FROM pii_policy_audit WHERE group_id IS NULL")
    with op.batch_alter_table("pii_policy_audit") as batch:
        batch.drop_column("value")
        batch.alter_column("group_id", existing_type=sa.Text(), nullable=False)
