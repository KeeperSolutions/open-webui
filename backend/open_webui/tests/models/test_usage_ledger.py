"""Tests for models/usage_ledger.py — ledger table queries."""
import datetime
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from open_webui.models.usage_ledger import UsageLedger, UsageLedgerTable


@pytest.fixture(scope="module")
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    # Create only the usage_ledger table — avoids FK errors from unrelated tables
    UsageLedger.__table__.create(engine, checkfirst=True)
    yield engine
    UsageLedger.__table__.drop(engine)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.query(UsageLedger).delete()
    session.commit()
    session.close()


@pytest.fixture
def ledger(db_session):
    """UsageLedgerTable instance with patched get_db pointing to in-memory session."""
    @contextmanager
    def _get_db():
        yield db_session

    with patch("open_webui.models.usage_ledger.get_db", _get_db):
        yield UsageLedgerTable()


def _now_epoch() -> int:
    return int(time.time())


def _month_start() -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    return int(datetime.datetime(now.year, now.month, 1, tzinfo=datetime.timezone.utc).timestamp())


def _make_row(obs_id: str, user: str, cost_eur: float | None = 0.01, observed_at: int | None = None) -> dict:
    return {
        "langfuse_observation_id": obs_id,
        "user_id": user,
        "model": "gpt-4o",
        "tokens_input": 100,
        "tokens_output": 50,
        "tokens_total": 150,
        "cost_usd": (cost_eur * 1.15) if cost_eur is not None else None,
        "eur_usd_rate": 1.15 if cost_eur is not None else None,
        "cost_eur": cost_eur,
        "observed_at": observed_at if observed_at is not None else _month_start() + 3600,
    }


class TestBulkInsertIgnore:
    def test_inserts_new_rows_and_returns_count(self, ledger):
        rows = [_make_row("obs_001", "a@x.com"), _make_row("obs_002", "b@x.com")]
        inserted = ledger.bulk_insert_ignore(rows)
        assert inserted == 2

    def test_skips_duplicate_observation_id(self, ledger):
        ledger.bulk_insert_ignore([_make_row("obs_dup", "a@x.com")])
        inserted = ledger.bulk_insert_ignore([_make_row("obs_dup", "a@x.com")])
        assert inserted == 0

    def test_returns_zero_for_empty_list(self, ledger):
        assert ledger.bulk_insert_ignore([]) == 0

    def test_partial_insert_when_some_duplicates(self, ledger):
        ledger.bulk_insert_ignore([_make_row("obs_existing", "a@x.com")])
        inserted = ledger.bulk_insert_ignore([
            _make_row("obs_existing", "a@x.com"),
            _make_row("obs_new_unique", "a@x.com"),
        ])
        assert inserted == 1


class TestGetMaxObservedAt:
    def test_returns_none_on_empty_table(self, ledger):
        assert ledger.get_max_observed_at() is None

    def test_returns_correct_max(self, ledger):
        ts1 = _month_start() + 1000
        ts2 = _month_start() + 5000
        ledger.bulk_insert_ignore([
            _make_row("obs_max_a", "a@x.com", observed_at=ts1),
            _make_row("obs_max_b", "a@x.com", observed_at=ts2),
        ])
        assert ledger.get_max_observed_at() == ts2


class TestBulkUpsertCosts:
    def test_updates_null_cost_row(self, ledger):
        ledger.bulk_insert_ignore([_make_row("obs_upsert_a", "u@x.com", cost_eur=None)])
        updated = ledger.bulk_upsert_costs([{
            "langfuse_observation_id": "obs_upsert_a",
            "cost_usd": 0.05,
            "eur_usd_rate": 1.15,
            "cost_eur": 0.05 / 1.15,
        }])
        assert updated == 1
        result = ledger.get_cost_eur_for_user_current_month("u@x.com")
        assert result == pytest.approx(0.05 / 1.15)

    def test_skips_already_priced_row(self, ledger):
        ledger.bulk_insert_ignore([_make_row("obs_upsert_b", "u2@x.com", cost_eur=0.05)])
        updated = ledger.bulk_upsert_costs([{
            "langfuse_observation_id": "obs_upsert_b",
            "cost_usd": 0.99,
            "eur_usd_rate": 1.15,
            "cost_eur": 0.99 / 1.15,
        }])
        assert updated == 0
        result = ledger.get_cost_eur_for_user_current_month("u2@x.com")
        assert result == pytest.approx(0.05)

    def test_returns_zero_for_empty_input(self, ledger):
        assert ledger.bulk_upsert_costs([]) == 0

    def test_skips_rows_without_cost_usd(self, ledger):
        ledger.bulk_insert_ignore([_make_row("obs_upsert_c", "u3@x.com", cost_eur=None)])
        updated = ledger.bulk_upsert_costs([{
            "langfuse_observation_id": "obs_upsert_c",
            "cost_usd": None,
            "eur_usd_rate": None,
            "cost_eur": None,
        }])
        assert updated == 0

    def test_skips_rows_where_ecb_unavailable(self, ledger):
        # cost_usd present but cost_eur=None (ECB was down during rescan) — must not
        # write NULL back onto NULL or claim a backfill occurred.
        ledger.bulk_insert_ignore([_make_row("obs_upsert_d", "u4@x.com", cost_eur=None)])
        updated = ledger.bulk_upsert_costs([{
            "langfuse_observation_id": "obs_upsert_d",
            "cost_usd": 0.05,
            "eur_usd_rate": None,
            "cost_eur": None,
        }])
        assert updated == 0
        # cost_eur should still be NULL, not changed
        assert ledger.get_cost_eur_for_user_current_month("u4@x.com") == 0.0

    def test_sets_has_data_after_update(self, ledger):
        ledger.bulk_insert_ignore([_make_row("obs_upsert_e", "u5@x.com", cost_eur=None)])
        ledger._has_data = False  # reset to simulate a fresh process that hasn't inserted yet
        ledger.bulk_upsert_costs([{
            "langfuse_observation_id": "obs_upsert_e",
            "cost_usd": 0.05,
            "eur_usd_rate": 1.15,
            "cost_eur": 0.05 / 1.15,
        }])
        assert ledger._has_data

    def test_bulk_update_multiple_rows(self, ledger):
        ledger.bulk_insert_ignore([
            _make_row("obs_upsert_f1", "u6@x.com", cost_eur=None),
            _make_row("obs_upsert_f2", "u6@x.com", cost_eur=None),
        ])
        updated = ledger.bulk_upsert_costs([
            {"langfuse_observation_id": "obs_upsert_f1", "cost_usd": 0.10, "eur_usd_rate": 1.15, "cost_eur": 0.10 / 1.15},
            {"langfuse_observation_id": "obs_upsert_f2", "cost_usd": 0.20, "eur_usd_rate": 1.15, "cost_eur": 0.20 / 1.15},
        ])
        assert updated == 2
        result = ledger.get_cost_eur_for_user_current_month("u6@x.com")
        assert result == pytest.approx((0.10 + 0.20) / 1.15)


class TestGetCostEurForUserCurrentMonth:
    def test_sums_current_month_cost_for_user(self, ledger):
        ledger.bulk_insert_ignore([
            _make_row("obs_cm_a", "user@x.com", cost_eur=0.10),
            _make_row("obs_cm_b", "user@x.com", cost_eur=0.05),
        ])
        result = ledger.get_cost_eur_for_user_current_month("user@x.com")
        assert result == pytest.approx(0.15)

    def test_excludes_null_cost_eur_rows(self, ledger):
        ledger.bulk_insert_ignore([
            _make_row("obs_null_a", "nulluser@x.com", cost_eur=0.10),
            _make_row("obs_null_b", "nulluser@x.com", cost_eur=None),
        ])
        result = ledger.get_cost_eur_for_user_current_month("nulluser@x.com")
        assert result == pytest.approx(0.10)

    def test_excludes_other_users(self, ledger):
        ledger.bulk_insert_ignore([
            _make_row("obs_other_a", "other@x.com", cost_eur=0.50),
            _make_row("obs_mine_a", "mine@x.com", cost_eur=0.10),
        ])
        result = ledger.get_cost_eur_for_user_current_month("mine@x.com")
        assert result == pytest.approx(0.10)

    def test_excludes_previous_month_rows(self, ledger):
        prev_month = _month_start() - 86400  # one day before month start
        ledger.bulk_insert_ignore([
            _make_row("obs_prev_a", "prevuser@x.com", cost_eur=0.20, observed_at=prev_month),
            _make_row("obs_curr_a", "prevuser@x.com", cost_eur=0.05),
        ])
        result = ledger.get_cost_eur_for_user_current_month("prevuser@x.com")
        assert result == pytest.approx(0.05)

    def test_returns_zero_for_unknown_user(self, ledger):
        assert ledger.get_cost_eur_for_user_current_month("nobody@x.com") == 0.0


class TestGetCostEurForUsersCurrentMonth:
    def test_returns_per_user_dict(self, ledger):
        ledger.bulk_insert_ignore([
            _make_row("obs_dict_a", "alpha@x.com", cost_eur=0.10),
            _make_row("obs_dict_b", "beta@x.com", cost_eur=0.20),
        ])
        result = ledger.get_cost_eur_for_users_current_month(["alpha@x.com", "beta@x.com"])
        assert result.get("alpha@x.com", 0.0) == pytest.approx(0.10)
        assert result.get("beta@x.com", 0.0) == pytest.approx(0.20)

    def test_returns_empty_dict_for_empty_input(self, ledger):
        assert ledger.get_cost_eur_for_users_current_month([]) == {}

    def test_missing_user_not_in_result(self, ledger):
        result = ledger.get_cost_eur_for_users_current_month(["ghost@x.com"])
        assert result.get("ghost@x.com", 0.0) == 0.0
