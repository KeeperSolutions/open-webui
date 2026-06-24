"""merge heads

Revision ID: d4e5f6a7b8c9
Revises: a7b8c9d0e1f2, c3d4e5f6a7b8
Create Date: 2026-06-25 00:00:00.000000

"""

from typing import Union

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, tuple, None] = ("a7b8c9d0e1f2", "c3d4e5f6a7b8")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
