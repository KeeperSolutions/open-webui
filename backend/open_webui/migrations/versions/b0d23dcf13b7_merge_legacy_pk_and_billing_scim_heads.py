"""merge legacy-pk and billing/scim heads

Revision ID: b0d23dcf13b7
Revises: 461111b60977, 81de4454640d
Create Date: 2026-07-24 00:00:00.000000

Both chains share b2c3d4e5f6a7_add_scim_column_to_user_table as a common
ancestor but became separate heads: 81de4454640d already merged
b2c3d4e5f6a7 with this fork's billing/credits chain (1782400005), while a
second, independent chain (Skills/access-grant/chat_message/note/calendar/
automations/shared_chat tables, ending at 461111b60977_add_missing_
primary_keys_to_legacy_) continued directly off b2c3d4e5f6a7 without ever
being rebased onto 81de4454640d's merge point. Joins them into a single
head — no schema changes of its own.
"""
from typing import Sequence, Union

revision: str = 'b0d23dcf13b7'
down_revision: Union[str, tuple, None] = ('461111b60977', '81de4454640d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
