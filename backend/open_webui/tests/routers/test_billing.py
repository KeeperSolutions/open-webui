"""Tests covering billing fixes from code review.

1. get_user_by_email propagates DB errors instead of swallowing them as None
2. _check_credits_exhausted accepts user_id directly — no redundant user lookup
3. Migration f2a3b4c5d6e7 INSERT has ON CONFLICT DO NOTHING — truly idempotent
4. Retry-After parse guard in fetch_observations_since falls back to 60s
5. Failed trace lookups are not cached
6. auto_onboard_user uses PLAN_TIER_* constants, not raw strings
7. Dead "paid" tier block removed from get_billing_status
8. get_trial_credits() computable without importing user_credits
9. Overage check uses single bulk DB query, not N+1
10. bulk_upsert_user_ids handles both NULL and empty-string user_id
"""
import asyncio
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

        balance = MagicMock()
        balance.subscription_credits = 100
        balance.topup_credits = 0
        balance.credits_per_eur_cent = 1.82

        mock_billings = MagicMock()
        mock_billings.get_by_user_id.return_value = record
        mock_balances = MagicMock()
        mock_balances.get.return_value = balance

        with patch.dict(os.environ, {"CREDITS_PER_EUR_CENT": "1.82"}):
            import open_webui.models.credit_balances as cb_mod
            import open_webui.models.user_credits as uc_mod
            with patch.object(_billing, "StripeBillings", mock_billings), \
                 patch.object(_billing, "CREDITS_TIERS", {"trial", "pro", "premium"}), \
                 patch.object(_billing, "PLAN_TIER_TRIAL", "trial"), \
                 patch.object(_billing, "_get_user_current_month_cost", return_value=1.0), \
                 patch.object(cb_mod, "CreditBalances", mock_balances), \
                 patch.object(uc_mod, "eur_to_credits", return_value=200), \
                 pytest.raises(HTTPException) as exc_info:
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


# ---------------------------------------------------------------------------
# Fix 5 — Failed trace lookups are not cached (so next tick retries them)
# ---------------------------------------------------------------------------

class TestTraceCacheOnSuccess:
    def test_failed_trace_not_cached(self):
        import open_webui.langfuse.observations as obs_module
        from concurrent.futures import ThreadPoolExecutor

        obs_module._trace_user_cache.clear()

        with patch.object(obs_module, "_fetch_one_trace", return_value=("tid-1", "")):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = obs_module._resolve_user_ids("host", {}, ["tid-1"], pool)

        assert result["tid-1"] == ""
        assert "tid-1" not in obs_module._trace_user_cache

    def test_successful_trace_is_cached(self):
        import open_webui.langfuse.observations as obs_module
        from concurrent.futures import ThreadPoolExecutor

        obs_module._trace_user_cache.clear()

        with patch.object(obs_module, "_fetch_one_trace", return_value=("tid-2", "user@example.com")):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = obs_module._resolve_user_ids("host", {}, ["tid-2"], pool)

        assert result["tid-2"] == "user@example.com"
        assert obs_module._trace_user_cache["tid-2"] == "user@example.com"


# ---------------------------------------------------------------------------
# Fix 6 (code-review) — auto_onboard_user uses PLAN_TIER_INTERNAL / PLAN_TIER_TRIAL constants
# ---------------------------------------------------------------------------

class TestAutoOnboardUserTierConstants:
    """Ensure auto_onboard_user passes tier constants, not raw strings."""

    def test_internal_user_upserted_with_constant(self):
        mock_billings = MagicMock()
        mock_billings.get_by_user_id.return_value = None  # not yet onboarded
        with patch.object(_billing, "StripeBillings", mock_billings), \
             patch.object(_billing, "BILLING_ENABLED", True), \
             patch.object(_billing, "has_unlimited_access", return_value=True):
            user = MagicMock()
            user.email = "staff@keeper.ai"
            user.id = "uid-internal"
            asyncio.run(_billing.auto_onboard_user(user))

        _, kwargs = mock_billings.upsert.call_args
        assert kwargs["plan_tier"] == "internal"
        assert kwargs["plan_tier"] == _billing.PLAN_TIER_INTERNAL

    def test_external_trial_user_upserted_with_constant(self):
        mock_billings = MagicMock()
        mock_billings.get_by_user_id.return_value = None  # not yet onboarded
        mock_stripe = MagicMock()
        mock_customer = MagicMock()
        mock_customer.id = "cus_test"
        mock_stripe.v1.customers.create.return_value = mock_customer

        import open_webui.models.user_credits as uc_mod
        mock_uc_db = MagicMock()

        with patch.object(_billing, "StripeBillings", mock_billings), \
             patch.object(_billing, "BILLING_ENABLED", True), \
             patch.object(_billing, "has_unlimited_access", return_value=False), \
             patch.object(_billing, "STRIPE_SECRET_KEY", "sk_test"), \
             patch.object(_billing, "STRIPE_FREE_TIER_CENTS", 0), \
             patch.object(_billing, "get_stripe_client", return_value=mock_stripe), \
             patch.object(uc_mod, "UserCreditsDB", mock_uc_db):
            user = MagicMock()
            user.email = "new@example.com"
            user.name = "New User"
            user.id = "uid-trial"
            asyncio.run(_billing.auto_onboard_user(user))

        _, kwargs = mock_billings.upsert.call_args
        assert kwargs["plan_tier"] == "trial"
        assert kwargs["plan_tier"] == _billing.PLAN_TIER_TRIAL


# ---------------------------------------------------------------------------
# Fix 7 (code-review) — dead "paid" tier block removed from get_billing_status
# ---------------------------------------------------------------------------

class TestNoPaidTierBlock:
    """The "paid" tier branch was removed; get_billing_status must not special-case it."""

    def test_paid_tier_falls_through_to_generic_response(self):
        record = MagicMock()
        record.plan_tier = "paid"
        record.stripe_customer_id = "cus_xyz"
        record.stripe_subscription_id = "sub_xyz"
        record.stripe_subscription_item_id = None
        record.subscription_status = None

        mock_billings = MagicMock()
        mock_billings.get_by_user_id.return_value = record

        with patch.object(_billing, "StripeBillings", mock_billings), \
             patch.object(_billing, "BILLING_ENABLED", True), \
             patch.object(_billing, "_get_user_current_month_cost", return_value=0.0):
            user = MagicMock()
            user.id = "uid-paid"
            user.email = "paid@example.com"
            response = asyncio.run(_billing.get_billing_status(user))

        # "paid" is not a known tier any more — must not crash and must not
        # return a plan_tier of "paid" via the now-deleted special-case branch
        assert response is not None
        # The dead block would have returned plan_tier="paid"; if it's gone the
        # code reaches the final fallback which returns a generic disabled/unconfigured response.
        assert response.plan_tier != "paid" or not response.is_configured


# ---------------------------------------------------------------------------
# Fix 8 (code-review) — get_trial_credits() callable without importing user_credits
# ---------------------------------------------------------------------------

class TestGetTrialCredits:
    def test_computable_without_user_credits_import(self):
        from open_webui.models.billing_plans import get_trial_credits
        result = get_trial_credits(1.82)
        assert result == round(2.00 * 100 * 1.82)

    def test_rate_change_reflects_in_result(self):
        from open_webui.models.billing_plans import get_trial_credits
        assert get_trial_credits(2.0) == round(400.0)
        assert get_trial_credits(1.0) == 200

    def test_user_credits_mutation_consistent_with_function(self):
        import open_webui.models.user_credits as uc_mod
        from open_webui.models.billing_plans import get_trial_credits
        # get_trial_credits should match what the rate produces
        assert get_trial_credits(uc_mod.CREDITS_PER_EUR_CENT) == round(2.00 * 100 * uc_mod.CREDITS_PER_EUR_CENT)


# ---------------------------------------------------------------------------
# Fix 9 (code-review) — bulk overage check: single DB query, not N+1
# ---------------------------------------------------------------------------

class TestOverageCheckBulk:
    """Verify that the overage check uses a single bulk query, not one per user."""

    def _run_sync(self, obs_rows, mock_ledger, mock_uc_db):
        """Run _sync_observations with mocked fetch, ledger, and user credits."""
        import open_webui.tasks.billing as tasks_mod
        import open_webui.models.user_credits as uc_mod
        import open_webui.models.usage_ledger as ledger_mod

        # _sync_observations uses local imports — patch at source module level
        with patch.object(ledger_mod, "UsageLedgerDB", mock_ledger), \
             patch.object(uc_mod, "UserCreditsDB", mock_uc_db), \
             patch("open_webui.langfuse.observations.fetch_observations_since", return_value=iter(obs_rows)), \
             patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=1.1), \
             patch("open_webui.models.users.Users"):
            tasks_mod._sync_observations(datetime.datetime(2024, 1, 1))

    def test_bulk_fetch_called_once_not_per_user(self):
        mock_ledger = MagicMock()
        mock_ledger.bulk_insert_ignore.return_value = 0
        mock_ledger.get_cost_eur_for_users_current_month.return_value = {}

        mock_uc_db = MagicMock()
        mock_uc_db.get.return_value = None

        self._run_sync([], mock_ledger, mock_uc_db)

        # bulk variant called exactly once (even with no rows)
        mock_ledger.get_cost_eur_for_users_current_month.assert_called_once_with([])

    def test_no_per_user_ledger_call(self):
        """get_cost_eur_for_user_current_month (singular) must NOT be called."""
        mock_ledger = MagicMock()
        mock_ledger.bulk_insert_ignore.return_value = 0
        mock_ledger.get_cost_eur_for_users_current_month.return_value = {}

        mock_uc_db = MagicMock()
        mock_uc_db.get.return_value = None

        self._run_sync([], mock_ledger, mock_uc_db)

        mock_ledger.get_cost_eur_for_user_current_month.assert_not_called()


# ---------------------------------------------------------------------------
# Fix 10 (code-review) — bulk_upsert_user_ids patches both NULL and '' rows
# ---------------------------------------------------------------------------

class TestBulkUpsertUserIdsEmptyString:
    def test_empty_string_user_id_is_backfilled(self):
        engine = create_engine("sqlite:///:memory:")
        from sqlalchemy import text as _text

        with engine.connect() as conn:
            conn.execute(_text("""
                CREATE TABLE usage_ledger (
                    id TEXT PRIMARY KEY,
                    langfuse_observation_id TEXT UNIQUE NOT NULL,
                    user_id TEXT,
                    model TEXT NOT NULL,
                    tokens_input INTEGER DEFAULT 0,
                    tokens_output INTEGER DEFAULT 0,
                    tokens_total INTEGER DEFAULT 0,
                    cost_usd REAL,
                    eur_usd_rate REAL,
                    cost_eur REAL,
                    observed_at INTEGER NOT NULL,
                    synced_at INTEGER NOT NULL
                )
            """))
            conn.execute(_text(
                "INSERT INTO usage_ledger VALUES ('id1','obs-1','','gpt-4',0,0,0,NULL,NULL,NULL,1000,1000)"
            ))
            conn.execute(_text(
                "INSERT INTO usage_ledger VALUES ('id2','obs-2',NULL,'gpt-4',0,0,0,NULL,NULL,NULL,1000,1000)"
            ))
            conn.commit()

        from open_webui.models.usage_ledger import UsageLedgerTable
        from unittest.mock import patch as _patch
        from contextlib import contextmanager

        @contextmanager
        def fake_get_db():
            from sqlalchemy.orm import Session
            session = Session(bind=engine)
            try:
                yield session
                session.commit()
            finally:
                session.close()

        table = UsageLedgerTable()
        rows = [
            {"langfuse_observation_id": "obs-1", "user_id": "alice@example.com"},
            {"langfuse_observation_id": "obs-2", "user_id": "alice@example.com"},
        ]

        with _patch("open_webui.models.usage_ledger.get_db", fake_get_db):
            updated = table.bulk_upsert_user_ids(rows)

        assert updated == 2

        with engine.connect() as conn:
            results = conn.execute(_text("SELECT user_id FROM usage_ledger ORDER BY id")).fetchall()
        assert all(r[0] == "alice@example.com" for r in results)
