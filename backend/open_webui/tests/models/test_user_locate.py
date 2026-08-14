"""Tests for `Users.get_users(locate=...)` — TRAU-536.

The property under test is that `position` indexes into the FULL ordered list,
not into the page being returned. That distinction is the whole reason the
parameter exists: the caller asks "which page is this person on", and a position
measured against the page would answer "0" for everyone.

The page arithmetic itself (`position // PAGE_ITEM_COUNT + 1`) lives in the
route and is exercised here against the same constant, so a change to the page
size cannot quietly desynchronise the two.
"""

import sys
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from open_webui.models.users import User, UsersTable
from open_webui.routers.users import PAGE_ITEM_COUNT, _list_filter


# 70 users is enough to cross the page boundary twice at a page size of 30.
USER_COUNT = 70


@pytest.fixture(scope="module")
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    User.__table__.create(engine, checkfirst=True)
    yield engine
    User.__table__.drop(engine)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    now = int(time.time())
    for i in range(USER_COUNT):
        session.add(
            User(
                id=f"u{i:03d}",
                # Names run backwards against creation order, so a test that
                # passes under both orderings cannot be passing by accident.
                name=f"User {USER_COUNT - i:03d}",
                email=f"u{i:03d}@example.com",
                role="user",
                # Distinct timestamps: equal ones make the order undefined and
                # the test flaky rather than wrong.
                created_at=now + i,
                updated_at=now + i,
                last_active_at=now + i,
            )
        )
    session.commit()
    yield session
    session.rollback()
    session.query(User).delete()
    session.commit()
    session.close()


@pytest.fixture
def users(db_session):
    """`UsersTable` bound to the in-memory session.

    ⚠️ The patch is required, not cosmetic: `DATABASE_ENABLE_SESSION_SHARING` is
    off, so `get_db_context` ignores a session passed as an argument and opens a
    real one. Without this the tests silently run against the developer's own
    database — which is how the first run of this file reported `total: 6`.
    """

    @contextmanager
    def _get_db_context(db=None):
        yield db_session

    with patch("open_webui.models.users.get_db_context", _get_db_context):
        yield UsersTable(), db_session


def locate(users, user_id, order_by="created_at", direction="asc"):
    table, session = users
    result = table.get_users(
        filter=_list_filter(order_by=order_by, direction=direction),
        skip=0,
        limit=1,
        db=session,
        locate=user_id,
    )
    return result


class TestPosition:
    def test_first_user_is_position_zero(self, users):
        assert locate(users, "u000")["position"] == 0

    def test_position_is_an_index_into_the_whole_list_not_the_page(self, users):
        # The call asks for a single-row page; the position must ignore that.
        result = locate(users, "u045")
        assert len(result["users"]) == 1
        assert result["position"] == 45

    def test_last_user(self, users):
        assert locate(users, f"u{USER_COUNT - 1:03d}")["position"] == USER_COUNT - 1

    def test_unknown_user_has_no_position(self, users):
        assert locate(users, "nobody")["position"] is None

    def test_position_is_none_when_not_asked_for(self, users):
        table, session = users
        result = table.get_users(filter=_list_filter(), skip=0, limit=5, db=session)
        assert result["position"] is None

    def test_total_is_unaffected_by_locating(self, users):
        assert locate(users, "u010")["total"] == USER_COUNT


class TestPositionFollowsTheOrdering:
    """The same user sits in different places under different sorts."""

    def test_reversing_the_direction_mirrors_the_position(self, users):
        asc = locate(users, "u010", "created_at", "asc")["position"]
        desc = locate(users, "u010", "created_at", "desc")["position"]
        assert asc == 10
        assert desc == USER_COUNT - 1 - 10

    def test_a_different_key_gives_a_different_position(self, users):
        # Names run backwards against creation order by construction.
        by_created = locate(users, "u010", "created_at", "asc")["position"]
        by_name = locate(users, "u010", "name", "asc")["position"]
        assert by_created == 10
        assert by_name == USER_COUNT - 1 - 10


class TestPageArithmetic:
    """`position // PAGE_ITEM_COUNT + 1`, as the route computes it."""

    def page_of(self, position):
        return position // PAGE_ITEM_COUNT + 1

    @pytest.mark.parametrize(
        "index, expected_page",
        [
            (0, 1),
            (PAGE_ITEM_COUNT - 1, 1),
            (PAGE_ITEM_COUNT, 2),  # first row of page 2 — the boundary
            (PAGE_ITEM_COUNT * 2 - 1, 2),
            (PAGE_ITEM_COUNT * 2, 3),
        ],
    )
    def test_boundaries(self, users, index, expected_page):
        position = locate(users, f"u{index:03d}")["position"]
        assert position == index
        assert self.page_of(position) == expected_page

    def test_every_user_lands_on_a_page_that_actually_contains_them(self, users):
        """The end-to-end claim, checked for all 70 rather than argued for."""
        table, session = users
        for i in range(USER_COUNT):
            uid = f"u{i:03d}"
            position = locate(users, uid)["position"]
            page = self.page_of(position)
            listed = table.get_users(
                filter=_list_filter(order_by="created_at", direction="asc"),
                skip=(page - 1) * PAGE_ITEM_COUNT,
                limit=PAGE_ITEM_COUNT,
                db=session,
            )
            assert uid in [u.id for u in listed["users"]], f"{uid} missing from page {page}"
