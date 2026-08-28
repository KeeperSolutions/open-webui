"""Add shared_chat table and migrate existing shares

Revision ID: c1d2e3f4a5b6
Revises: e1f2a3b4c5d6
Create Date: 2026-04-16 23:00:00.000000

"""

import logging
import time
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

revision = 'c1d2e3f4a5b6'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None

# Lightweight table references for the parts of this migration that don't
# touch encrypted content (plain-column reads/deletes, and access_grant,
# which has no encrypted columns). shared_chat.chat is intentionally NOT
# given a Core table reference here — it's an EncryptedJSONField (same as
# chat.chat), so every read/write of it goes through the ORM `SharedChat`/
# `Chat` models instead, to get correct encrypt-on-write/decrypt-on-read.
chat_t = sa.table(
    'chat',
    sa.column('id', sa.Text),
    sa.column('user_id', sa.Text),
)

chat_message_t = sa.table(
    'chat_message',
    sa.column('chat_id', sa.Text),
)

shared_chat_id_t = sa.table(
    'shared_chat',
    sa.column('id', sa.Text),
)

access_grant_t = sa.table(
    'access_grant',
    sa.column('id', sa.Text),
    sa.column('resource_type', sa.Text),
    sa.column('resource_id', sa.Text),
    sa.column('principal_type', sa.Text),
    sa.column('principal_id', sa.Text),
    sa.column('permission', sa.Text),
    sa.column('created_at', sa.BigInteger),
)


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Create shared_chat table (idempotent). `chat` is sa.Text() at the
    # DB level, not sa.JSON() — it stores through EncryptedJSONField
    # (impl = types.Text; see internal/db.py and models/shared_chats.py),
    # the same convention chat.chat uses (see 7e5b5dc7342b_init.py).
    if 'shared_chat' not in tables:
        op.create_table(
            'shared_chat',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('chat_id', sa.Text(), sa.ForeignKey('chat.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('title', sa.Text(), nullable=True),
            sa.Column('chat', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=True),
            sa.Column('updated_at', sa.BigInteger(), nullable=True),
        )

    # 2. Migrate existing shared-* rows.
    #
    # Both chat.chat and shared_chat.chat are EncryptedJSONField (see
    # internal/db.py) — a TypeDecorator that transparently
    # encrypts/decrypts, but only when SQLAlchemy knows the column's
    # declared type. Raw Core columns/inserts bypass that entirely: reading
    # chat.chat via a bare sa.column('chat', sa.JSON()) would return raw
    # ciphertext, and inserting into shared_chat.chat via a raw Core
    # insert() would write an already-decrypted dict as plaintext instead
    # of encrypting it. Querying the real `Chat` class and
    # constructing real `SharedChat` ORM instances gets correct
    # decrypt-on-read/encrypt-on-write for both, for free.
    #
    # No batching of the chat payload itself: unlike the chat_message
    # backfill (which batches small per-message metadata dicts), each row
    # read here carries the FULL decrypted chat.chat blob — up to 30-44MB
    # per chat on staging. Flushing immediately after each row keeps peak
    # memory bounded to roughly one decrypted blob at a time regardless of
    # how many shared-% rows exist, rather than scaling with a batch size.
    # yield_per is kept small for the same reason on the read side.
    from open_webui.models.chats import Chat
    from open_webui.models.shared_chats import SharedChat

    session = Session(bind=conn)

    query = (
        session.query(Chat.id, Chat.user_id, Chat.title, Chat.chat, Chat.created_at, Chat.updated_at)
        .filter(Chat.user_id.like('shared-%'))
        .execution_options(yield_per=100, stream_results=True)
    )

    total_migrated = 0

    for chat_id, user_id, title, chat_data, created_at, updated_at in query:
        share_token = chat_id
        original_chat_id = user_id.replace('shared-', '', 1)

        # Verify original chat still exists (plain-column read, no
        # encryption concern — only user_id is needed here).
        original = conn.execute(
            sa.select(chat_t.c.user_id).where(chat_t.c.id == original_chat_id)
        ).fetchone()

        if not original:
            continue

        # Check if shared_chat record already exists (idempotent)
        existing_shared = conn.execute(
            sa.select(shared_chat_id_t.c.id).where(shared_chat_id_t.c.id == share_token)
        ).fetchone()

        if not existing_shared:
            session.add(
                SharedChat(
                    id=share_token,
                    chat_id=original_chat_id,
                    user_id=original.user_id,
                    title=title,
                    chat=chat_data,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
            session.commit()

        # Check if access_grant record already exists (idempotent)
        existing_grant = conn.execute(
            sa.select(access_grant_t.c.id).where(
                sa.and_(
                    access_grant_t.c.resource_type == 'shared_chat',
                    access_grant_t.c.resource_id == original_chat_id,
                    access_grant_t.c.principal_type == 'user',
                    access_grant_t.c.principal_id == '*',
                    access_grant_t.c.permission == 'read',
                )
            )
        ).fetchone()

        if not existing_grant:
            # Create user:*:read grant for backward compat
            conn.execute(
                access_grant_t.insert().values(
                    id=str(uuid.uuid4()),
                    resource_type='shared_chat',
                    resource_id=original_chat_id,
                    principal_type='user',
                    principal_id='*',
                    permission='read',
                    created_at=created_at or int(time.time()),
                )
            )

        total_migrated += 1
        if total_migrated % 100 == 0:
            log.info(f'shared_chat migration progress: {total_migrated} rows migrated...')

    session.close()

    log.info(f'Migrated {total_migrated} shared-chat rows into shared_chat table')

    # 3. Clean up old phantom rows (plain-column deletes, no encryption
    # concern).
    conn.execute(
        chat_message_t.delete().where(
            chat_message_t.c.chat_id.in_(sa.select(chat_t.c.id).where(chat_t.c.user_id.like('shared-%')))
        )
    )
    conn.execute(chat_t.delete().where(chat_t.c.user_id.like('shared-%')))


def downgrade():
    conn = op.get_bind()

    # Read shared_chat.chat via the ORM `SharedChat` class (not a raw Core
    # select) and re-insert via the ORM `Chat` model (not a raw Core
    # insert) — both columns are EncryptedJSONField, so a Core select would
    # return ciphertext and a Core insert of an already-decrypted dict
    # would write plaintext. Committed per-row for the same peak-memory
    # reasoning as upgrade() (each row can carry a large decrypted blob).
    from open_webui.models.chats import Chat
    from open_webui.models.shared_chats import SharedChat

    session = Session(bind=conn)

    # Select individual columns, not the SharedChat entity — an ORM entity
    # select triggers SQLAlchemy 2.0's automatic result .unique() dedup,
    # which raises InvalidRequestError when combined with yield_per. See
    # upgrade()'s equivalent column-select for the same reason.
    query = (
        session.query(
            SharedChat.id,
            SharedChat.chat_id,
            SharedChat.title,
            SharedChat.chat,
            SharedChat.created_at,
            SharedChat.updated_at,
        )
        .execution_options(yield_per=100, stream_results=True)
    )

    total_restored = 0
    for shared_chat_id, chat_id, title, chat_data, created_at, updated_at in query:
        phantom_chat = Chat(
            id=shared_chat_id,
            user_id=f'shared-{chat_id}',
            title=title,
            chat=chat_data,
            created_at=created_at,
            updated_at=updated_at,
            archived=False,
            meta={},
        )
        session.add(phantom_chat)
        session.commit()
        total_restored += 1

    session.close()

    log.info(f'Restored {total_restored} phantom shared-chat rows')

    conn.execute(access_grant_t.delete().where(access_grant_t.c.resource_type == 'shared_chat'))
    op.drop_table('shared_chat')
