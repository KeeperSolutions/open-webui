"""Convert chat.chat column from JSON to TEXT for encryption support

Revision ID: 6927529b47e2
Revises: d8d905b57f4e
Create Date: 2026-05-19 00:00:00.000000

Migration 242a2047eae0 converted chat.chat from TEXT to JSON/JSONB.
EncryptedJSONField stores ENC1:... strings which are not valid JSON, so the
column must be TEXT before any encrypted writes can happen.

On PostgreSQL the USING clause casts the existing JSON value to its text
representation (identical bytes, no data loss). On SQLite the column is
already stored as TEXT at the storage level, so upgrade()/downgrade() are
no-ops there.

Encryption status is inferred from the ENC1: prefix — no separate column needed.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "6927529b47e2"
down_revision: Union[str, None] = "d8d905b57f4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # USING chat::text safely casts existing JSON/JSONB to its text
        # representation — identical bytes, no data loss.
        op.execute(
            "ALTER TABLE chat ALTER COLUMN chat TYPE TEXT USING chat::text"
        )
    # SQLite stores everything as TEXT at the storage level regardless of the
    # declared column type, so no DDL is needed here. (A plain op.alter_column
    # would emit "ALTER TABLE chat ALTER COLUMN chat TYPE TEXT", which is not
    # valid SQLite syntax and would need op.batch_alter_table instead.)


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Casting TEXT back to JSON will fail for any rows that contain ENC1:
        # ciphertext. Only run downgrade before encrypted writes have occurred.
        op.execute(
            "ALTER TABLE chat ALTER COLUMN chat TYPE JSON USING chat::json"
        )
    # SQLite: no DDL was performed in upgrade(), so nothing to revert here.
