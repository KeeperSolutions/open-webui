"""Add pii_policy_audit table

Audit log for administrative mutations of the team PII masking policy.

The four event types exist in the schema from the start even though this
release only ever emits the two `policy_*` ones. The membership events
(`member_added` / `member_removed`) ship in the row action that follows, and
adding them now means the schema is not migrated twice for one feature.

`user_id` is nullable because it is NULL for `policy_*` and required for
`member_*`. That is not expressible as a portable DDL constraint, so it is
enforced by the single writer (models/pii_policy_audit.py) and covered by tests.

Revision ID: 1782400006
Revises: 81de4454640d
Create Date: 2026-08-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782400006"
down_revision: Union[str, None] = "81de4454640d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "pii_policy_audit",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        # policy_enabled | policy_disabled | member_added | member_removed
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("group_id", sa.Text(), nullable=False),
        # NULL for policy_*, set for member_*
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Text(), nullable=False),
        # Denormalised: the acting admin's account may be deleted later
        sa.Column("actor_email", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("event_ts", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "pii_policy_audit_group_id_event_ts_idx",
        "pii_policy_audit",
        ["group_id", "event_ts"],
    )


def downgrade():
    op.drop_index("pii_policy_audit_group_id_event_ts_idx", table_name="pii_policy_audit")
    op.drop_table("pii_policy_audit")
