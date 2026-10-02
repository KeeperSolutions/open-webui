import time
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, Chats, chat_row_for_update

MESSAGE = {'id': 'message-1', 'role': 'assistant', 'parentId': None, 'childrenIds': [], 'content': 'Hi'}


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Chat.__table__.create)
        await conn.run_sync(ChatMessage.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = int(time.time())
    async with factory() as session:
        session.add(
            Chat(
                id='chat-1',
                user_id='user-1',
                title='Chat',
                chat={'history': {'messages': {'message-1': MESSAGE}, 'currentId': 'message-1'}},
                created_at=now,
                updated_at=now,
                archived=False,
                pinned=False,
                meta={},
            )
        )
        await session.commit()
    with (
        patch('open_webui.internal.db.AsyncSessionLocal', factory),
        patch('open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING', False),
    ):
        yield factory
    await engine.dispose()


def test_the_chat_row_is_locked_so_other_workers_wait_for_the_update():
    sql = str(chat_row_for_update('chat-1').compile(dialect=postgresql.dialect()))

    assert 'FOR UPDATE' in sql


@pytest.mark.asyncio
async def test_update_is_built_from_the_current_messages_and_saved(session_factory):
    seen = []

    def build(messages):
        seen.append(messages['message-1']['content'])
        return 'message-1', {'documents': [{'file_id': 'file-1'}]}

    await Chats.update_message_with_lock('chat-1', build, touch=False)

    chat = await Chats.get_chat_by_id('chat-1')
    assert seen == ['Hi']
    assert chat.chat['history']['messages']['message-1']['documents'] == [{'file_id': 'file-1'}]
    assert chat.chat['history']['messages']['message-1']['content'] == 'Hi'
    async with session_factory() as session:
        row = await session.get(ChatMessage, 'chat-1-message-1')
    assert row is not None


@pytest.mark.asyncio
async def test_nothing_is_written_when_build_returns_none(session_factory):
    before = await Chats.get_chat_by_id('chat-1')

    assert await Chats.update_message_with_lock('chat-1', lambda messages: None) is None

    after = await Chats.get_chat_by_id('chat-1')
    assert after.chat == before.chat and after.updated_at == before.updated_at
