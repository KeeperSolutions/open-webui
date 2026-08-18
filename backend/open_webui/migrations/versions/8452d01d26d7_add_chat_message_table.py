"""Add chat_message table

Revision ID: 8452d01d26d7
Revises: 374d2f66af06
Create Date: 2026-02-01 04:00:00.000000

"""

import json
import logging
import time
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

revision: str = '8452d01d26d7'
down_revision: Union[str, None] = '374d2f66af06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BATCH_SIZE = 5000


def _flush_batch(conn, table, batch):
    """
    Insert a batch of messages, falling back to row-by-row on error.

    Tries a single bulk insert first (fast path). If that fails (e.g. due to
    a duplicate key), falls back to individual inserts wrapped in savepoints
    so the rest of the batch can still succeed.
    """
    savepoint = conn.begin_nested()
    try:
        conn.execute(sa.insert(table), batch)
        savepoint.commit()
        return len(batch), 0
    except Exception:
        savepoint.rollback()
        # Batch failed - insert one-by-one to isolate the bad row(s)
        inserted = 0
        failed = 0
        for msg in batch:
            sp = conn.begin_nested()
            try:
                conn.execute(sa.insert(table).values(**msg))
                sp.commit()
                inserted += 1
            except Exception as e:
                sp.rollback()
                failed += 1
                log.warning(f'Failed to insert message {msg["id"]}: {e}')
        return inserted, failed


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if 'chat_message' in existing_tables:
        return  # Already created — skip everything

    # Step 1: Create table
    op.create_table(
        'chat_message',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('chat_id', sa.Text(), nullable=False, index=True),
        sa.Column('user_id', sa.Text(), index=True),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('parent_id', sa.Text(), nullable=True),
        sa.Column('model_id', sa.Text(), nullable=True, index=True),
        sa.Column('done', sa.Boolean(), default=True),
        sa.Column('status_history', sa.JSON(), nullable=True),
        sa.Column('error', sa.JSON(), nullable=True),
        sa.Column('usage', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), index=True),
        sa.Column('updated_at', sa.BigInteger()),
        sa.ForeignKeyConstraint(['chat_id'], ['chat.id'], ondelete='CASCADE'),
    )

    # Create composite indexes
    op.create_index('chat_message_chat_parent_idx', 'chat_message', ['chat_id', 'parent_id'])
    op.create_index('chat_message_model_created_idx', 'chat_message', ['model_id', 'created_at'])
    op.create_index('chat_message_user_created_idx', 'chat_message', ['user_id', 'created_at'])

    # Step 2: Backfill from existing chats.
    #
    # This reads chat.chat through the ORM `Chat` model (not a raw Core
    # `sa.table()`/`select()`), because chat.chat is an `EncryptedJSONField`
    # (see internal/db.py) — a TypeDecorator that transparently
    # decrypts/deserializes on read, but ONLY when SQLAlchemy knows the
    # column's declared type. A bare Core column (`sa.column('chat', sa.JSON())`)
    # bypasses that entirely and would read raw ciphertext for encrypted
    # chats, silently skipping/corrupting their message history. Binding a
    # plain Session to Alembic's connection and querying the real `Chat`
    # class gets correct decryption for free.
    #
    # chat_message itself only stores metadata (role/model_id/usage/status/
    # timestamps) — message content/output/files/sources/embeds are
    # deliberately NOT backfilled here, since nothing reads them back out of
    # this table (see models/chat_messages.py) and duplicating message text
    # into an unencrypted table would undermine chat.chat's own encryption.
    from open_webui.models.chats import Chat

    conn = op.get_bind()
    session = Session(bind=conn)

    chat_message_table = sa.table(
        'chat_message',
        sa.column('id', sa.Text()),
        sa.column('chat_id', sa.Text()),
        sa.column('user_id', sa.Text()),
        sa.column('role', sa.Text()),
        sa.column('parent_id', sa.Text()),
        sa.column('model_id', sa.Text()),
        sa.column('done', sa.Boolean()),
        sa.column('status_history', sa.JSON()),
        sa.column('error', sa.JSON()),
        sa.column('usage', sa.JSON()),
        sa.column('created_at', sa.BigInteger()),
        sa.column('updated_at', sa.BigInteger()),
    )

    now = int(time.time())
    messages_batch = []
    total_inserted = 0
    total_failed = 0

    # yield_per is intentionally small (not the usual ~1000): chat.chat is
    # an EncryptedJSONField, so each row is decrypted into a full Python dict
    # before this loop ever sees it. A handful of chats on staging run
    # 30-44MB of encrypted JSON each — buffering hundreds of those at once
    # OOM-killed a 2Gi container. A small prefetch window keeps peak memory
    # closer to a few large rows at a time instead of hundreds.
    query = (
        session.query(Chat.id, Chat.user_id, Chat.chat)
        .filter(~Chat.user_id.like('shared-%'))
        .execution_options(yield_per=100, stream_results=True)
    )

    for chat_id, user_id, chat_data in query:
        if not chat_data:
            continue

        # EncryptedJSONField.process_result_value already returns a dict
        # (it json.loads()s internally), but guard against legacy string
        # rows just in case.
        if isinstance(chat_data, str):
            import json

            try:
                chat_data = json.loads(chat_data)
            except Exception:
                continue

        history = chat_data.get('history', {})
        if not isinstance(history, dict):
            continue

        messages = history.get('messages', {})
        if not isinstance(messages, dict):
            continue

        for message_id, message in messages.items():
            if not isinstance(message, dict):
                continue

            role = message.get('role')
            if not role:
                continue

            timestamp = message.get('timestamp', now)

            try:
                timestamp = int(float(timestamp))
            except Exception:
                timestamp = now

            # Normalize timestamp: convert ms to seconds, validate range
            if timestamp > 10_000_000_000:
                timestamp = timestamp // 1000
            # Must be after 2020 and not too far in the future
            if timestamp < 1577836800 or timestamp > now + 86400:
                timestamp = now

            usage = message.get('usage')
            if not usage:
                info = message.get('info') or {}
                usage = info.get('usage') if isinstance(info, dict) else None

            messages_batch.append(
                {
                    'id': f'{chat_id}-{message_id}',
                    'chat_id': chat_id,
                    'user_id': user_id,
                    'role': role,
                    'parent_id': message.get('parentId') or message.get('parent_id'),
                    'model_id': message.get('model') or message.get('model_id'),
                    'done': message.get('done', True),
                    'status_history': message.get('statusHistory') or message.get('status_history'),
                    'error': message.get('error'),
                    'usage': usage,
                    'created_at': timestamp,
                    'updated_at': timestamp,
                }
            )

            # Flush batch when full
            if len(messages_batch) >= BATCH_SIZE:
                inserted, failed = _flush_batch(conn, chat_message_table, messages_batch)
                total_inserted += inserted
                total_failed += failed
                if total_inserted % 50000 < BATCH_SIZE:
                    log.info(f'Migration progress: {total_inserted} messages inserted...')
                messages_batch.clear()

    # Flush remaining messages
    if messages_batch:
        inserted, failed = _flush_batch(conn, chat_message_table, messages_batch)
        total_inserted += inserted
        total_failed += failed

    session.close()

    log.info(f'Backfilled {total_inserted} messages into chat_message table ({total_failed} failed)')


def downgrade() -> None:
    op.drop_index('chat_message_user_created_idx', table_name='chat_message')
    op.drop_index('chat_message_model_created_idx', table_name='chat_message')
    op.drop_index('chat_message_chat_parent_idx', table_name='chat_message')
    op.drop_table('chat_message')
