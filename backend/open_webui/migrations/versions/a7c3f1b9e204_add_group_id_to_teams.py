"""Add teams.group_id — the back-reference a team's PII policy group is found by

Revision ID: a7c3f1b9e204
Revises: ce5cc6fe333b
Create Date: 2026-08-20 19:05:00.000000

⚠️ This migration adds a COLUMN and nothing else. It creates no group, links no
team and moves nobody between groups — that is the bridge migration, and it is a
separate revision on purpose: rolling back a column is not the same operation as
rolling back somebody's membership of a policy group, and bundling them would
mean the cheap half could not be undone without the dangerous half.

The column is `UNIQUE`, and that is the load-bearing part rather than tidiness:
without it two teams can point at the same group and "the team's own group"
stops being a single answer — which is the one thing `team_group_kind` has to be
able to give.

Implemented as a unique INDEX rather than a table constraint because SQLite
cannot add a constraint to an existing table without rebuilding it, and a
rebuild is exactly the kind of operation this revision is trying not to be. The
index is equivalent for every purpose here, and it is how `team_members` already
expresses the same idea (`uq_team_members_user_id`).

NULL is not constrained by a unique index on either SQLite or Postgres, so any
number of teams may have no group — which is the normal state until the bridge
migration runs.
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
