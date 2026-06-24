"""Tests for models/user_credits.py — UserCreditsTable."""
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from open_webui.models.user_credits import (
    UserCredits,
    UserCreditsTable,
    eur_to_credits,
    credits_to_eur,
)


@pytest.fixture(scope="module")
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    UserCredits.__table__.create(engine, checkfirst=True)
    yield engine
    UserCredits.__table__.drop(engine)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.query(UserCredits).delete()
    session.commit()
    session.close()


@pytest.fixture
def table(db_session):
    @contextmanager
    def _get_db():
        yield db_session

    with patch("open_webui.models.user_credits.get_db", _get_db):
        yield UserCreditsTable()


class TestConversionHelpers:
    def test_eur_to_credits_standard_rate(self):
        assert eur_to_credits(1.0, 1.82) == 182

    def test_eur_to_credits_trial(self):
        assert eur_to_credits(2.0, 1.82) == 364

    def test_eur_to_credits_after_rate_change(self):
        assert eur_to_credits(1.0, 2.0) == 200

    def test_credits_to_eur_standard_rate(self):
        assert credits_to_eur(182, 1.82) == 1.0

    def test_credits_to_eur_zero_rate_returns_zero(self):
        assert credits_to_eur(182, 0.0) == 0.0


class TestGetBalance:
    def test_returns_zero_for_unknown_user(self, table):
        assert table.get_balance("nobody@example.com") == 0

    def test_get_returns_none_for_unknown_user(self, table):
        assert table.get("nobody@example.com") is None


class TestSetPlan:
    def test_creates_row_on_first_call(self, table):
        result = table.set_plan("alice@example.com", 1300, 1.82)
        assert result.balance == 1300
        assert result.credits_per_eur_cent == 1.82
        assert result.user_id == "alice@example.com"

    def test_resets_balance_on_renewal(self, table):
        table.set_plan("bob@example.com", 3800, 1.82)
        result = table.set_plan("bob@example.com", 1300, 1.82)
        assert result.balance == 1300

    def test_locks_new_rate_on_renewal(self, table):
        table.set_plan("carol@example.com", 1300, 1.82)
        result = table.set_plan("carol@example.com", 1300, 2.0)
        assert result.credits_per_eur_cent == 2.0

    def test_no_duplicate_row_on_repeat_calls(self, table, db_session):
        table.set_plan("dave@example.com", 364, 1.82)
        table.set_plan("dave@example.com", 1300, 1.82)
        count = db_session.query(UserCredits).filter_by(user_id="dave@example.com").count()
        assert count == 1

    def test_get_balance_after_set_plan(self, table):
        table.set_plan("eve@example.com", 3800, 1.82)
        assert table.get_balance("eve@example.com") == 3800


class TestAddCredits:
    def test_creates_row_when_user_has_no_row(self, table):
        new_balance = table.add_credits("frank@example.com", 500)
        assert new_balance == 500

    def test_increments_existing_balance(self, table):
        table.set_plan("grace@example.com", 1300, 1.82)
        new_balance = table.add_credits("grace@example.com", 200)
        assert new_balance == 1500

    def test_does_not_change_rate_on_top_up(self, table):
        table.set_plan("heidi@example.com", 1300, 1.82)
        table.add_credits("heidi@example.com", 200)
        row = table.get("heidi@example.com")
        assert row.credits_per_eur_cent == 1.82

    def test_multiple_top_ups_accumulate(self, table):
        table.set_plan("ivan@example.com", 1300, 1.82)
        table.add_credits("ivan@example.com", 100)
        table.add_credits("ivan@example.com", 100)
        assert table.get_balance("ivan@example.com") == 1500


class TestRateIsolation:
    def test_existing_user_rate_unaffected_by_set_plan_on_other_user(self, table):
        table.set_plan("judy@example.com", 1300, 1.82)
        table.set_plan("kate@example.com", 1300, 2.0)
        judy = table.get("judy@example.com")
        assert judy.credits_per_eur_cent == 1.82
