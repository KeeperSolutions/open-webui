"""Tests for tasks/billing.py's admin alert dispatch — unpriced model, pricing
recovered, and ECB unreachable alerts, all gated through BillingAlertStateDB
instead of the old in-process-only dedup sets.
"""
import datetime
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from open_webui.models.billing_alert_state import (
    ALERT_TYPE_UNPRICED_MODEL,
    BillingAlertState,
    BillingAlertStateTable,
)


@pytest.fixture
def alert_state_db():
    """Real BillingAlertStateTable backed by a fresh in-memory SQLite DB per test,
    so cooldown/dedup behavior is exercised for real rather than call-counted on a mock."""
    engine = create_engine("sqlite:///:memory:")
    BillingAlertState.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    session = Session()

    @contextmanager
    def _get_db():
        yield session

    with patch("open_webui.models.billing_alert_state.get_db", _get_db):
        yield BillingAlertStateTable()

    session.close()
    BillingAlertState.__table__.drop(engine)


def _obs(obs_id: str, model: str | None, cost_usd: float | None) -> dict:
    return {
        "id": obs_id,
        "userId": "user@example.com",
        "model": model,
        "usage": {"input": 10, "output": 5, "total": 15},
        "startTime": "2024-01-01T00:00:00.000Z",
        "calculatedTotalCost": cost_usd,
    }


def _run_sync(obs_rows, alert_state_db):
    """Run _sync_observations with Langfuse/ledger/users mocked, but the real
    (in-memory) BillingAlertStateDB so cooldown gating is genuinely exercised."""
    import open_webui.models.billing_alert_state as alert_state_mod
    import open_webui.tasks.billing as tasks_mod

    mock_ledger = MagicMock()
    mock_ledger.bulk_insert_ignore.return_value = 0
    mock_ledger.get_cost_eur_for_users_current_month.return_value = {}
    mock_ledger.get_models_with_recent_priced_rows.return_value = []

    mock_admin = MagicMock()
    mock_admin.email = "admin@example.com"

    with patch.object(alert_state_mod, "BillingAlertStateDB", alert_state_db), \
         patch("open_webui.models.usage_ledger.UsageLedgerDB", mock_ledger), \
         patch("open_webui.langfuse.observations.fetch_observations_since", return_value=iter(obs_rows)), \
         patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=1.1), \
         patch("open_webui.models.users.Users.get_super_admin_user", return_value=mock_admin), \
         patch("open_webui.utils.email.send_unpriced_models_email", return_value=True) as mock_send_unpriced, \
         patch("open_webui.utils.email.send_model_pricing_recovered_email", return_value=True) as mock_send_recovered:
        tasks_mod._sync_observations(datetime.datetime(2024, 1, 1))

    return mock_send_unpriced, mock_send_recovered


class TestUnpricedModelAlertDispatch:
    def test_sends_alert_for_newly_unpriced_model(self, alert_state_db):
        mock_send_unpriced, _ = _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        mock_send_unpriced.assert_called_once()
        assert mock_send_unpriced.call_args.kwargs["model_names"] == ["grok-4.6"]

    def test_does_not_resend_within_cooldown(self, alert_state_db):
        # First poll: model is unpriced, alert fires.
        mock_send_unpriced, _ = _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        assert mock_send_unpriced.call_count == 1

        # Second poll, same model still unpriced: cooldown hasn't elapsed, must not re-alert.
        mock_send_unpriced2, _ = _run_sync([_obs("o2", "grok-4.6", None)], alert_state_db)
        mock_send_unpriced2.assert_not_called()

    def test_resends_after_cooldown_elapses(self, alert_state_db):
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)

        # Force the recorded alert to look like it happened long ago.
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        with patch("open_webui.models.billing_alert_state.BILLING_ALERT_COOLDOWN_SECONDS", -1):
            mock_send_unpriced2, _ = _run_sync([_obs("o2", "grok-4.6", None)], alert_state_db)
            mock_send_unpriced2.assert_called_once()

    def test_falls_back_to_unknown_when_model_and_name_missing(self, alert_state_db):
        obs = _obs("o1", None, None)
        obs.pop("model")
        mock_send_unpriced, _ = _run_sync([obs], alert_state_db)
        mock_send_unpriced.assert_called_once()
        # Dedup key is "unknown" (see BillingAlertStateDB usage elsewhere), but the email
        # shows a friendlier label clarifying it's a missing-data case, not a real model id.
        assert mock_send_unpriced.call_args.kwargs["model_names"] == ["unknown (missing model field)"]

    def test_dedup_key_stays_plain_unknown_despite_display_label(self, alert_state_db):
        """The friendlier email label must not leak into the dedup/ledger key - otherwise
        every poll would look like a "new" unpriced model and cooldown would never engage."""
        obs = _obs("o1", None, None)
        obs.pop("model")
        _run_sync([obs], alert_state_db)
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "unknown")

        obs2 = _obs("o2", None, None)
        obs2.pop("model")
        mock_send_unpriced2, _ = _run_sync([obs2], alert_state_db)
        mock_send_unpriced2.assert_not_called()


class TestPricingRecoveredAlertDispatch:
    def test_sends_recovery_alert_once_model_is_priced_again(self, alert_state_db):
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        _, mock_send_recovered = _run_sync([_obs("o2", "grok-4.6", 1.0)], alert_state_db)
        mock_send_recovered.assert_called_once()
        assert mock_send_recovered.call_args.kwargs["model_names"] == ["grok-4.6"]
        assert not alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

    def test_relapse_immediately_after_recovery_is_still_cooldown_gated(self, alert_state_db):
        """should_alert() only looks at last_alerted_at, not status - record_recovered()
        doesn't reset the clock. So a model that relapses right after recovering does NOT
        get an immediate second alert; it waits out the same cooldown as any other repeat."""
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        _run_sync([_obs("o2", "grok-4.6", 1.0)], alert_state_db)
        assert not alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        mock_send_unpriced, _ = _run_sync([_obs("o3", "grok-4.6", None)], alert_state_db)
        mock_send_unpriced.assert_not_called()

        # Once the cooldown elapses, the relapse alert does go out.
        with patch("open_webui.models.billing_alert_state.BILLING_ALERT_COOLDOWN_SECONDS", -1):
            mock_send_unpriced2, _ = _run_sync([_obs("o4", "grok-4.6", None)], alert_state_db)
            mock_send_unpriced2.assert_called_once()
