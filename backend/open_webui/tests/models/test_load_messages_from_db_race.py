"""Regression tests for the chat-history race condition
(md-docs/chat-history-race-condition.md).

Chat message persistence is two separate, independently-committed writes
inside Chats.upsert_message_to_chat_by_id_and_message_id
(backend/open_webui/models/chats.py):

  1. The `chat.history` JSON blob is written and committed.
  2. A "dual write" of the same message into the normalized `chat_message`
     table happens afterwards, in its own transaction — best-effort, wrapped
     in try/except so a failure only logs a warning.

Between those two commits there's a window where a concurrent
load_messages_from_db() lookup for the new message finds nothing. Before the
fix, that silently left form_data['messages'] as a system-prompt-only
payload, which the LLM provider rejects (hit production once on
2026-09-15). The fix adds a bounded retry to load_messages_from_db() and
raises explicitly (via _require_db_messages) when it's still empty after
retrying, instead of forwarding an incomplete history.

No live API key or network calls — everything here runs against an
in-memory SQLite DB and asserts on load_messages_from_db()'s and
_require_db_messages()'s real Python-level output.
"""
import asyncio
import time
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.chats import Chat, Chats
from open_webui.utils.middleware import (
    _LOAD_MESSAGES_RETRY_ATTEMPTS,
    _LOAD_MESSAGES_RETRY_DELAY_SECONDS,
    _require_db_messages,
    load_messages_from_db,
)


def _make_chat(chat_id, user_id, history_messages=None):
    now = int(time.time())
    return Chat(
        id=chat_id,
        user_id=user_id,
        title='New Chat',
        chat={
            'history': {'messages': history_messages or {}, 'currentId': None},
            'messages': [{'role': 'system', 'content': 'You are a helpful assistant.'}],
        },
        created_at=now,
        updated_at=now,
        archived=False,
        pinned=False,
        meta={},
    )


def _user_message(message_id, content, parent_id=None):
    return {
        'id': message_id,
        'role': 'user',
        'parentId': parent_id,
        'childrenIds': [],
        'content': content,
        'timestamp': int(time.time()),
    }


USER = 'user-abc'


class TestRequireDbMessages:
    def test_raises_when_no_messages_found(self):
        with pytest.raises(HTTPException) as exc_info:
            _require_db_messages(None, 'chat-1', 'msg-1')
        assert exc_info.value.status_code == 409

    def test_raises_on_empty_list(self):
        with pytest.raises(HTTPException):
            _require_db_messages([], 'chat-1', 'msg-1')

    def test_returns_messages_unchanged_when_present(self):
        messages = [{'id': 'msg-1', 'role': 'user', 'content': 'hi'}]
        assert _require_db_messages(messages, 'chat-1', 'msg-1') == messages


@pytest.mark.asyncio
class TestLoadMessagesFromDbRetryBehavior:
    async def test_retries_bounded_number_of_times_without_real_delay(self):
        """Pins the retry contract: exactly _LOAD_MESSAGES_RETRY_ATTEMPTS
        lookups, sleeping _LOAD_MESSAGES_RETRY_ATTEMPTS - 1 times, with no
        real wall-clock delay (asyncio.sleep is mocked out)."""
        call_count = 0

        async def always_miss(chat_id):
            nonlocal call_count
            call_count += 1
            return None

        with patch(
            'open_webui.utils.middleware.Chats.get_messages_map_by_chat_id', always_miss
        ), patch('open_webui.utils.middleware.asyncio.sleep', AsyncMock()) as mock_sleep:
            result = await load_messages_from_db('chat-1', 'msg-1')

        assert result is None
        assert call_count == _LOAD_MESSAGES_RETRY_ATTEMPTS
        assert mock_sleep.call_count == _LOAD_MESSAGES_RETRY_ATTEMPTS - 1
        for call in mock_sleep.call_args_list:
            assert call.args[0] == _LOAD_MESSAGES_RETRY_DELAY_SECONDS

    async def test_succeeds_immediately_when_message_is_already_present(self):
        """No retry needed — the common case must not be slowed down."""
        messages_map = {
            'msg-1': {'id': 'msg-1', 'role': 'user', 'content': 'hi', 'parentId': None, 'childrenIds': []},
        }
        call_count = 0

        async def hit(chat_id):
            nonlocal call_count
            call_count += 1
            return messages_map

        with patch(
            'open_webui.utils.middleware.Chats.get_messages_map_by_chat_id', hit
        ), patch('open_webui.utils.middleware.asyncio.sleep', AsyncMock()) as mock_sleep:
            result = await load_messages_from_db('chat-1', 'msg-1')

        assert result is not None
        assert result[0]['content'] == 'hi'
        assert call_count == 1
        mock_sleep.assert_not_called()

    async def test_succeeds_on_a_later_attempt_once_message_appears(self):
        """Simulates the real-world recovery: the first attempt(s) miss
        (dual-write not landed yet), a later attempt within the retry
        budget finds it."""
        messages_map = {
            'msg-1': {'id': 'msg-1', 'role': 'user', 'content': 'hi', 'parentId': None, 'childrenIds': []},
        }
        call_count = 0

        async def miss_then_hit(chat_id):
            nonlocal call_count
            call_count += 1
            if call_count < _LOAD_MESSAGES_RETRY_ATTEMPTS:
                return None
            return messages_map

        with patch(
            'open_webui.utils.middleware.Chats.get_messages_map_by_chat_id', miss_then_hit
        ), patch('open_webui.utils.middleware.asyncio.sleep', AsyncMock()):
            result = await load_messages_from_db('chat-1', 'msg-1')

        assert result is not None
        assert result[0]['content'] == 'hi'
        assert call_count == _LOAD_MESSAGES_RETRY_ATTEMPTS


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Chat.__table__.create)
        await conn.run_sync(ChatMessage.__table__.create)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
class TestLoadMessagesFromDbClosesTheRealRace:
    async def test_read_during_dual_write_gap_recovers_the_users_message(self, db_engine):
        """
        True regression test against the real write path: forces the exact
        production timing gap (chat.history committed, chat_message
        dual-write still in flight) and calls load_messages_from_db()
        directly — the same function process_chat_payload() depends on.

        Fails before the fix (no retry loop: a lookup that lands inside the
        gap returns None immediately). Passes after the fix (the retry
        loop's later attempt succeeds once the held-open dual-write is
        released, which happens partway through the retry window — after
        the first attempt has already missed, and before the budget is
        exhausted).

        The chat is seeded with one PRIOR message already present in both
        chat_message and the legacy blob. This matters: Chats.
        get_messages_map_by_chat_id() only falls back to the legacy blob
        when the chat_message fast path returns None outright (no rows at
        all) or flags an *unresolved parent reference*. A brand-new leaf
        message that nothing points to yet triggers neither case — the fast
        path just returns a map that's silently missing its key, which is
        the actual production failure mode. Without a prior message, the
        fast path would return None on the very first chat_message query
        (no rows exist yet for this chat at all) and the legacy-blob
        fallback would mask the race this test exists to catch.
        """
        chat_id = str(uuid.uuid4())
        prior_message_id = 'msg-prior'
        user_message_id = 'msg-a'

        session_factory = async_sessionmaker(
            bind=db_engine, autocommit=False, autoflush=False, expire_on_commit=False
        )

        prior_message = _user_message(prior_message_id, 'Hello')
        async with session_factory() as setup_session:
            setup_session.add(_make_chat(chat_id, USER, history_messages={prior_message_id: prior_message}))
            await setup_session.commit()
            setup_session.add(
                ChatMessage(
                    id=f'{chat_id}-{prior_message_id}',
                    chat_id=chat_id,
                    user_id=USER,
                    role='user',
                    parent_id=None,
                    created_at=prior_message['timestamp'],
                    updated_at=prior_message['timestamp'],
                )
            )
            await setup_session.commit()

        message = _user_message(user_message_id, 'What is the capital of France?', parent_id=prior_message_id)

        original_upsert_message = ChatMessages.upsert_message

        # patch.object on an *instance* attribute does not auto-bind `self`
        # (unlike patching a class attribute), and chats.py calls
        # ChatMessages.upsert_message(...) with only keyword arguments — so
        # this replacement must not declare its own `self` parameter.
        async def delayed_upsert_message(*args, **kwargs):
            await asyncio.sleep(_LOAD_MESSAGES_RETRY_DELAY_SECONDS * 1.5)
            return await original_upsert_message(*args, **kwargs)

        with patch('open_webui.internal.db.AsyncSessionLocal', session_factory), patch(
            'open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING', False
        ), patch.object(ChatMessages, 'upsert_message', delayed_upsert_message):

            async def save_user_message():
                await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, user_message_id, message)

            save_task = asyncio.create_task(save_user_message())

            # Give save_user_message a moment to commit the chat.history
            # write and start (but not finish) the delayed dual-write,
            # before load_messages_from_db's first attempt runs.
            await asyncio.sleep(0.01)

            result = await asyncio.wait_for(
                load_messages_from_db(chat_id, user_message_id), timeout=15
            )
            await asyncio.wait_for(save_task, timeout=15)

        assert result is not None, (
            'load_messages_from_db returned None for a message that was '
            'already committed to chat.history — the dual-write race was not closed'
        )
        assert any(m.get('content') == 'What is the capital of France?' for m in result)
