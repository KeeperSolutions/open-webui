"""Tests for get_chats_by_user_id_and_search_text — title-only search after encryption."""

import time
import uuid

import pytest
import pytest_asyncio
from unittest.mock import patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from open_webui.models.chats import Chat, Chats


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Chat.__table__.create)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    with patch("open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING", True):
        yield session
    await session.close()
    await engine.dispose()


def _make_chat(user_id, title, content="some message body", archived=False):
    now = int(time.time())
    return Chat(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=title,
        chat={"messages": [{"role": "user", "content": content}]},
        created_at=now,
        updated_at=now,
        archived=archived,
        pinned=False,
        meta={},
    )


USER = "user-abc"


@pytest.mark.asyncio
class TestTitleOnlySearch:
    async def test_matches_title(self, db_session):
        db_session.add(_make_chat(USER, "Python decorators explained"))
        db_session.add(_make_chat(USER, "How to cook pasta"))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(USER, "Python", db=db_session)
        assert len(results) == 1
        assert results[0].title == "Python decorators explained"

    async def test_no_match_on_body_content(self, db_session):
        db_session.add(_make_chat(USER, "Unrelated title", content="Python decorators explained"))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(USER, "Python", db=db_session)
        assert len(results) == 0

    async def test_case_insensitive_title_match(self, db_session):
        db_session.add(_make_chat(USER, "Python Decorators"))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(USER, "python", db=db_session)
        assert len(results) == 1

    async def test_partial_title_match(self, db_session):
        db_session.add(_make_chat(USER, "Guide to async/await in Python"))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(USER, "async", db=db_session)
        assert len(results) == 1

    async def test_empty_search_returns_all(self, db_session):
        db_session.add(_make_chat(USER, "Chat one"))
        db_session.add(_make_chat(USER, "Chat two"))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(USER, "", db=db_session)
        assert len(results) == 2

    async def test_no_results_when_nothing_matches(self, db_session):
        db_session.add(_make_chat(USER, "How to bake bread"))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(
            USER, "quantum physics", db=db_session
        )
        assert len(results) == 0

    async def test_does_not_return_other_users_chats(self, db_session):
        db_session.add(_make_chat(USER, "Python tips"))
        db_session.add(_make_chat("other-user", "Python tips"))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(USER, "Python", db=db_session)
        assert len(results) == 1
        assert all(r.user_id == USER for r in results)

    async def test_archived_excluded_by_default(self, db_session):
        db_session.add(_make_chat(USER, "Python archived", archived=True))
        db_session.add(_make_chat(USER, "Python active", archived=False))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(USER, "Python", db=db_session)
        assert len(results) == 1
        assert results[0].title == "Python active"

    async def test_archived_included_when_flag_set(self, db_session):
        db_session.add(_make_chat(USER, "Python archived", archived=True))
        db_session.add(_make_chat(USER, "Python active", archived=False))
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(
            USER, "Python", include_archived=True, db=db_session
        )
        assert len(results) == 2

    async def test_encrypted_body_does_not_leak_into_search(self, db_session):
        chat = _make_chat(USER, "Unrelated title")
        chat.chat = "ENC1:AQAAAAFsomefakeciphertextbase64=="  # type: ignore[assignment]
        db_session.add(chat)
        await db_session.commit()

        results = await Chats.get_chats_by_user_id_and_search_text(USER, "ENC1", db=db_session)
        assert len(results) == 0
