"""Tests covering four billing fixes from code review:

1. get_user_by_email propagates DB errors instead of swallowing them as None
2. _check_credits_exhausted accepts user_id directly — no redundant user lookup
3. Migration f2a3b4c5d6e7 INSERT has ON CONFLICT DO NOTHING — truly idempotent
4. Retry-After parse guard in fetch_observations_since falls back to 60s
"""
import datetime
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------------------
# Fix 1 — get_user_by_email propagates DB errors
# ---------------------------------------------------------------------------

class TestGetUserByEmail:
    def test_returns_none_for_missing_user(self):
        @contextmanager
        def _get_db(db=None):
            session = MagicMock()
            session.query.return_value.filter_by.return_value.first.return_value = None
            yield session

        with patch("open_webui.models.users.get_db_context", _get_db):
            from open_webui.models.users import UsersTable
            assert UsersTable().get_user_by_email("nobody@example.com") is None

    def test_propagates_db_error(self):
        @contextmanager
        def _get_db(db=None):
            session = MagicMock()
            session.query.side_effect = Exception("DB connection lost")
            yield session

        with patch("open_webui.models.users.get_db_context", _get_db):
            from open_webui.models.users import UsersTable
            with pytest.raises(Exception, match="DB connection lost"):
                UsersTable().get_user_by_email("user@example.com")


# ---------------------------------------------------------------------------
# Fix 2 — _check_credits_exhausted takes user_id directly
# ---------------------------------------------------------------------------

# stripe is not installed in the test env — mock it before importing the router
import sys
sys.modules.setdefault("stripe", MagicMock())
import open_webui.routers.billing as _billing  # noqa: E402


class TestCheckCreditsExhausted:
    def test_no_user_table_lookup_performed(self):
        """Users.get_user_by_email must never be called — user_id is passed directly."""
        mock_billings = MagicMock()
        mock_billings.get_by_user_id.return_value = None
        mock_users = MagicMock()

        with patch.object(_billing, "StripeBillings", mock_billings), \
             patch.object(_billing, "CREDITS_TIERS", {"trial", "pro", "premium"}), \
             patch("open_webui.models.users.Users", mock_users):
            _billing._check_credits_exhausted("user@example.com", "user-uuid-123")
            mock_users.get_user_by_email.assert_not_called()

    def test_stripe_lookup_uses_user_id_arg(self):
        mock_billings = MagicMock()
        mock_billings.get_by_user_id.return_value = None

        with patch.dict(os.environ, {"CREDITS_PER_EUR_CENT": "1.82"}), \
             patch.object(_billing, "StripeBillings", mock_billings), \
             patch.object(_billing, "CREDITS_TIERS", {"trial", "pro", "premium"}):
            _billing._check_credits_exhausted("user@example.com", "user-uuid-123")
            mock_billings.get_by_user_id.assert_called_once_with("user-uuid-123")

    def test_skips_non_credits_plan(self):
        record = MagicMock()
        record.plan_tier = "internal"
        mock_billings = MagicMock()
        mock_billings.get_by_user_id.return_value = record

        with patch.object(_billing, "StripeBillings", mock_billings), \
             patch.object(_billing, "CREDITS_TIERS", {"trial", "pro", "premium"}):
            _billing._check_credits_exhausted("user@example.com", "user-uuid-123")

    def test_raises_402_when_credits_exhausted(self):
        from fastapi import HTTPException

        record = MagicMock()
        record.plan_tier = "pro"
        record.created_at = 0

        credits_row = MagicMock()
        credits_row.balance = 100
        credits_row.credits_per_eur_cent = 1.82

        mock_billings = MagicMock()
        mock_billings.get_by_user_id.return_value = record
        mock_uc_db = MagicMock()
        mock_uc_db.get.return_value = credits_row

        with patch.dict(os.environ, {"CREDITS_PER_EUR_CENT": "1.82"}), \
             patch.object(_billing, "StripeBillings", mock_billings), \
             patch.object(_billing, "CREDITS_TIERS", {"trial", "pro", "premium"}), \
             patch.object(_billing, "PLAN_TIER_TRIAL", "trial"), \
             patch.object(_billing, "_get_user_current_month_cost", return_value=1.0):
            import open_webui.models.user_credits as uc_mod
            with patch.object(uc_mod, "UserCreditsDB", mock_uc_db), \
                 patch.object(uc_mod, "eur_to_credits", return_value=200):
                with pytest.raises(HTTPException) as exc_info:
                    _billing._check_credits_exhausted("user@example.com", "user-uuid-123")
                assert exc_info.value.status_code == 402
                assert exc_info.value.detail == "credits_exhausted"


# ---------------------------------------------------------------------------
# Fix 3 — Migration ON CONFLICT DO NOTHING idempotency
# ---------------------------------------------------------------------------

@pytest.fixture
def migration_conn():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE "user" (id TEXT PRIMARY KEY, email TEXT NOT NULL)
        """))
        conn.execute(text("""
            CREATE TABLE stripe_billing (user_id TEXT PRIMARY KEY, plan_tier TEXT NOT NULL)
        """))
        conn.execute(text("""
            CREATE TABLE user_credits (
                id TEXT PRIMARY KEY,
                user_id TEXT UNIQUE NOT NULL,
                balance INTEGER NOT NULL,
                credits_per_eur_cent REAL NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """))
        conn.execute(text("INSERT INTO \"user\" VALUES ('uid-1', 'alice@example.com')"))
        conn.execute(text("INSERT INTO stripe_billing VALUES ('uid-1', 'pro')"))
        conn.commit()
        yield conn


class TestBackfillMigration:
    def _run_upgrade(self, conn):
        with patch.dict(os.environ, {"CREDITS_PER_EUR_CENT": "1.82"}):
            from open_webui.migrations.versions.f2a3b4c5d6e7_backfill_user_credits import upgrade
            with patch("open_webui.migrations.versions.f2a3b4c5d6e7_backfill_user_credits.op") as mock_op:
                mock_op.get_bind.return_value = conn
                upgrade()

    def test_inserts_row_keyed_by_email(self, migration_conn):
        self._run_upgrade(migration_conn)
        rows = migration_conn.execute(
            text("SELECT user_id, balance FROM user_credits")
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "alice@example.com"
        assert rows[0][1] == 1300

    def test_idempotent_on_second_run(self, migration_conn):
        self._run_upgrade(migration_conn)
        self._run_upgrade(migration_conn)  # must not raise
        count = migration_conn.execute(
            text("SELECT COUNT(*) FROM user_credits WHERE user_id = 'alice@example.com'")
        ).scalar()
        assert count == 1

    def test_skips_orphaned_stripe_billing_row(self, migration_conn):
        migration_conn.execute(text("INSERT INTO stripe_billing VALUES ('orphan-uid', 'trial')"))
        migration_conn.commit()
        self._run_upgrade(migration_conn)
        user_ids = {r[0] for r in migration_conn.execute(
            text("SELECT user_id FROM user_credits")
        ).fetchall()}
        assert "alice@example.com" in user_ids
        assert "orphan-uid" not in user_ids


# ---------------------------------------------------------------------------
# Fix 4 — Retry-After parse guard
# ---------------------------------------------------------------------------

class TestRetryAfterParsing:
    def _run_fetch(self, retry_after_value):
        import open_webui.langfuse.observations as obs_module

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = (
            {"Retry-After": retry_after_value} if retry_after_value is not None else {}
        )
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"data": [], "meta": {"totalPages": 1}}

        with patch("open_webui.langfuse.observations.requests.get", side_effect=[resp_429, resp_ok]), \
             patch("open_webui.langfuse.observations.time.sleep") as mock_sleep, \
             patch("open_webui.langfuse.observations.load_env", return_value=("pk", "sk", "https://host")), \
             patch("open_webui.langfuse.observations.auth_header", return_value={}):
            list(obs_module.fetch_observations_since(datetime.datetime(2024, 1, 1)))
            return mock_sleep

    def test_integer_retry_after_is_respected(self):
        mock_sleep = self._run_fetch("30")
        mock_sleep.assert_called_once_with(30)

    def test_http_date_retry_after_falls_back_to_60(self):
        mock_sleep = self._run_fetch("Fri, 27 Jun 2026 10:00:00 GMT")
        mock_sleep.assert_called_once_with(60)

    def test_missing_retry_after_defaults_to_60(self):
        mock_sleep = self._run_fetch(None)
        mock_sleep.assert_called_once_with(60)
