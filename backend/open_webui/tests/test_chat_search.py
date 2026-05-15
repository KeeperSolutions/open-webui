"""Tests for get_chats_by_user_id_and_search_text — title-only search after encryption."""

import time
import uuid

import pytest
from unittest.mock import patch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from open_webui.models.chats import Chat, Chats


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Chat.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    with patch("open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING", True):
        yield session
    session.close()
    Chat.__table__.drop(engine)


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


class TestTitleOnlySearch:
    def test_matches_title(self, db_session):
        db_session.add(_make_chat(USER, "Python decorators explained"))
        db_session.add(_make_chat(USER, "How to cook pasta"))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "Python", db=db_session)
        assert len(results) == 1
        assert results[0].title == "Python decorators explained"

    def test_no_match_on_body_content(self, db_session):
        db_session.add(_make_chat(USER, "Unrelated title", content="Python decorators explained"))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "Python", db=db_session)
        assert len(results) == 0

    def test_case_insensitive_title_match(self, db_session):
        db_session.add(_make_chat(USER, "Python Decorators"))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "python", db=db_session)
        assert len(results) == 1

    def test_partial_title_match(self, db_session):
        db_session.add(_make_chat(USER, "Guide to async/await in Python"))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "async", db=db_session)
        assert len(results) == 1

    def test_empty_search_returns_all(self, db_session):
        db_session.add(_make_chat(USER, "Chat one"))
        db_session.add(_make_chat(USER, "Chat two"))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "", db=db_session)
        assert len(results) == 2

    def test_no_results_when_nothing_matches(self, db_session):
        db_session.add(_make_chat(USER, "How to bake bread"))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "quantum physics", db=db_session)
        assert len(results) == 0

    def test_does_not_return_other_users_chats(self, db_session):
        db_session.add(_make_chat(USER, "Python tips"))
        db_session.add(_make_chat("other-user", "Python tips"))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "Python", db=db_session)
        assert len(results) == 1
        assert all(r.user_id == USER for r in results)

    def test_archived_excluded_by_default(self, db_session):
        db_session.add(_make_chat(USER, "Python archived", archived=True))
        db_session.add(_make_chat(USER, "Python active", archived=False))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "Python", db=db_session)
        assert len(results) == 1
        assert results[0].title == "Python active"

    def test_archived_included_when_flag_set(self, db_session):
        db_session.add(_make_chat(USER, "Python archived", archived=True))
        db_session.add(_make_chat(USER, "Python active", archived=False))
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(
            USER, "Python", include_archived=True, db=db_session
        )
        assert len(results) == 2

    def test_encrypted_body_does_not_leak_into_search(self, db_session):
        chat = _make_chat(USER, "Unrelated title")
        chat.chat = "ENC1:AQAAAAFsomefakeciphertextbase64=="  # type: ignore[assignment]
        db_session.add(chat)
        db_session.commit()

        results = Chats.get_chats_by_user_id_and_search_text(USER, "ENC1", db=db_session)
        assert len(results) == 0
