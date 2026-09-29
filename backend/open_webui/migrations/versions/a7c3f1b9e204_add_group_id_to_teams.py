"""Add teams.group_id, the reference to a team's PII policy group

Revision ID: a7c3f1b9e204
Revises: ce5cc6fe333b
Create Date: 2026-08-20 19:05:00.000000

Only adds the column. Creating groups and moving members is done by the bridge
migration (b6d1a4f0c7e2), kept separate so the column can be rolled back without
touching anyone's group membership.

The column must be unique: otherwise two teams could point at one group and
`team_group_kind` could not return a single team. It is a unique index rather
than a constraint because SQLite cannot add a constraint without rebuilding the
table; `uq_team_members_user_id` does the same. Unique indexes allow any number
of NULLs on SQLite and Postgres, so teams without a group are fine.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3f1b9e204"
down_revision: Union[str, None] = "ce5cc6fe333b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("teams", sa.Column("group_id", sa.Text(), nullable=True))
    op.create_index("uq_teams_group_id", "teams", ["group_id"], unique=True)


def downgrade():
    op.drop_index("uq_teams_group_id", table_name="teams")
    op.drop_column("teams", "group_id")
