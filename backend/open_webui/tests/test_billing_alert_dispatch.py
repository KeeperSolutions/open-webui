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


def _run_sync(
    obs_rows, alert_state_db, *,
    unpriced_send_succeeds=True, recovered_send_succeeds=True,
    deep_rescan=False, already_ledgered_ids=(),
):
    """Run _sync_observations with Langfuse/ledger/users mocked, but the real
    (in-memory) BillingAlertStateDB so cooldown gating is genuinely exercised.

    already_ledgered_ids simulates observations that exist in the ledger from an
    earlier poll (so bulk_insert_ignore reports them as duplicates) - used to
    exercise the deep-rescan cost-backfill path, where bulk_upsert_costs must be
    the thing that makes a previously-unpriced row's new price visible to
    classification, not bulk_insert_ignore (which sees only a duplicate id)."""
    import open_webui.models.billing_alert_state as alert_state_mod
    import open_webui.tasks.billing as tasks_mod

    mock_ledger = MagicMock()
    mock_ledger.bulk_insert_ignore.side_effect = (
        lambda rows: {r["langfuse_observation_id"] for r in rows if r["langfuse_observation_id"] not in already_ledgered_ids}
    )
    # Mirrors the real bulk_upsert_costs contract: only rows with both cost_usd and
    # cost_eur present get backfilled, returned as the set of ids actually written.
    mock_ledger.bulk_upsert_costs.side_effect = (
        lambda rows: {r["langfuse_observation_id"] for r in rows if r.get("cost_usd") is not None and r.get("cost_eur") is not None}
    )
    mock_ledger.bulk_upsert_user_ids.return_value = 0
    mock_ledger.get_cost_eur_for_users_current_month.return_value = {}
    mock_ledger.get_models_with_recent_priced_rows.return_value = []

    mock_admin = MagicMock()
    mock_admin.email = "admin@example.com"

    with patch.object(alert_state_mod, "BillingAlertStateDB", alert_state_db), \
         patch("open_webui.models.usage_ledger.UsageLedgerDB", mock_ledger), \
         patch("open_webui.langfuse.observations.fetch_observations_since", return_value=iter(obs_rows)), \
         patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=1.1), \
         patch("open_webui.models.users.Users.get_super_admin_user", return_value=mock_admin), \
         patch("open_webui.utils.email.send_unpriced_models_email", return_value=unpriced_send_succeeds) as mock_send_unpriced, \
         patch("open_webui.utils.email.send_model_pricing_recovered_email", return_value=recovered_send_succeeds) as mock_send_recovered:
        tasks_mod._sync_observations(datetime.datetime(2024, 1, 1), deep_rescan=deep_rescan)

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

    def test_genuine_model_named_unknown_is_not_relabeled(self, alert_state_db):
        """Copilot review finding: _display_model_name() used to string-match model=="unknown",
        which would also relabel a real observation whose model/name field is literally the
        string "unknown" (not missing) as if it were a missing-data case. The label must be
        driven by provenance (was the field actually absent), not the resulting value."""
        obs = _obs("o1", "unknown", None)  # model field IS present, its value happens to be "unknown"
        assert obs.get("model") == "unknown"
        mock_send_unpriced, _ = _run_sync([obs], alert_state_db)
        mock_send_unpriced.assert_called_once()
        assert mock_send_unpriced.call_args.kwargs["model_names"] == ["unknown"]

    def test_concurrent_claim_race_only_one_instance_sends(self, alert_state_db):
        """Copilot review finding: the old should_alert() -> send -> record_alerted()
        sequence was check-then-act, so two instances could both see should_alert()==True
        for the same newly-unpriced model before either recorded anything, and both would
        email. try_claim_alert() is meant to close that by making the claim atomic. Simulate
        two instances racing on the exact same due model by calling try_claim_alert directly,
        as _sync_observations does internally, and assert only one of them wins."""
        from open_webui.models.billing_alert_state import ALERT_TYPE_UNPRICED_MODEL

        instance_a_claimed = alert_state_db.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        instance_b_claimed = alert_state_db.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        assert instance_a_claimed is True
        assert instance_b_claimed is False

    def test_crashed_instance_orphaned_claim_does_not_block_alert_forever(self, alert_state_db):
        """Review finding: try_claim_alert() writes status=claiming before the email is sent.
        If the process crashes/restarts between the claim and the send, no exception runs, so
        release_claim() never fires - simulated here by calling try_claim_alert() directly
        (as a "crashed instance" would have) and never following up with confirm_alert() or
        release_claim(), exactly what a hard kill leaves behind. A subsequent poll must still
        be able to alert once BILLING_ALERT_CLAIM_TTL_SECONDS passes, rather than being stuck
        for the full (much longer) BILLING_ALERT_COOLDOWN_SECONDS."""
        from open_webui.models.billing_alert_state import ALERT_TYPE_UNPRICED_MODEL

        # "Crashed instance": claims but never confirms or releases.
        assert alert_state_db.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

        # A poll immediately after must not be able to send - the claim is still fresh, and
        # for all this poll knows the crashed instance might still be mid-send.
        mock_send_unpriced, _ = _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        mock_send_unpriced.assert_not_called()

        # Once the claim TTL (much shorter than the alert cooldown) has passed, a later poll
        # must be able to reclaim and actually send.
        with patch("open_webui.models.billing_alert_state.BILLING_ALERT_CLAIM_TTL_SECONDS", -1):
            mock_send_unpriced2, _ = _run_sync([_obs("o2", "grok-4.6", None)], alert_state_db)
            mock_send_unpriced2.assert_called_once()

    def test_recovered_model_relapse_reclaimable_after_cooldown(self, alert_state_db):
        """Bug found while fixing the above: try_claim_alert()'s reclaim WHERE clause only
        checked status=alerted (cooldown) or status=claiming (TTL) - it never accounted for
        status=recovered, which is what a model sits at after a confirmed recovery. A relapse
        to unpriced on a status=recovered model could never be reclaimed by try_claim_alert()
        at all, regardless of how much time passed, silently breaking every relapse alert."""
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        _run_sync([_obs("o2", "grok-4.6", 1.0)], alert_state_db)  # confirmed recovered
        assert not alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        with patch("open_webui.models.billing_alert_state.BILLING_ALERT_COOLDOWN_SECONDS", -1):
            mock_send_unpriced, _ = _run_sync([_obs("o3", "grok-4.6", None)], alert_state_db)
            mock_send_unpriced.assert_called_once()


class TestPricingRecoveredAlertDispatch:
    def test_sends_recovery_alert_once_model_is_priced_again(self, alert_state_db):
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        _, mock_send_recovered = _run_sync([_obs("o2", "grok-4.6", 1.0)], alert_state_db)
        mock_send_recovered.assert_called_once()
        assert mock_send_recovered.call_args.kwargs["model_names"] == ["grok-4.6"]
        assert not alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

    def test_deep_rescan_cost_backfill_triggers_recovery_alert(self, alert_state_db):
        """Code-review finding: classification used to look only at bulk_insert_ignore's
        newly-inserted set, which made every row the nightly deep rescan backfills invisible
        to it - those rows are, by definition, duplicates (already ledgered from an earlier
        poll), so a model recovering pricing only via the deep rescan never reached
        candidate_recovered and the admin never got the "pricing recovered" email. The fix
        folds bulk_upsert_costs' own returned ids into classification. Simulated here via
        already_ledgered_ids, since a real deep rescan re-fetches an observation that's
        already in the table from an earlier poll - its row is a duplicate from
        bulk_insert_ignore's point of view, and only visible through bulk_upsert_costs."""
        # Poll 1 (hot path): observation has no cost, model gets alerted unpriced.
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        # Nightly deep rescan re-fetches the SAME observation (now priced in Langfuse).
        # It is already in the ledger, so bulk_insert_ignore must report it as a duplicate -
        # only bulk_upsert_costs' own return value can surface the new price.
        _, mock_send_recovered = _run_sync(
            [_obs("o1", "grok-4.6", 1.0)],
            alert_state_db,
            deep_rescan=True,
            already_ledgered_ids={"o1"},
        )

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

    def test_concurrent_recovery_claim_race_only_one_instance_sends(self, alert_state_db):
        """Same race as the unpriced-model claim, but for the recovered transition: two
        instances could both compute the same newly-recovered model and both email before
        either recorded it. try_claim_recovery() makes the alerted->recovered flip atomic."""
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        instance_a_claimed = alert_state_db.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        instance_b_claimed = alert_state_db.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        assert instance_a_claimed is not None
        assert instance_b_claimed is None

    def test_same_poll_mixed_batch_does_not_send_both_unpriced_and_restored(self, alert_state_db):
        """Third finding from the investigation: a single poll whose batch contains both a
        without-usage and a with-usage observation for the same already-alerted model used to
        trigger "unpriced" and "restored" emails in the very same poll - directly
        contradictory, and the source of the ~1,000/~1,000 alert-spam pair observed in
        production. A model that both regains pricing AND produces a fresh unpriced
        observation in the same poll must not be claimed as recovered in that poll - the
        recovery alert is deferred to a later poll where it doesn't collide."""
        # Model is already in an alerted state from an earlier poll.
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        with patch("open_webui.models.billing_alert_state.BILLING_ALERT_COOLDOWN_SECONDS", -1):
            # Same poll: one new priced row and one new unpriced row for the same model.
            mock_send_unpriced, mock_send_recovered = _run_sync(
                [_obs("o2", "grok-4.6", 1.0), _obs("o3", "grok-4.6", None)],
                alert_state_db,
            )

        mock_send_recovered.assert_not_called()
        # The relapse alert itself is allowed to fire (cooldown forced open above) - what must
        # never happen is both firing together.
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

    def test_failed_recovery_send_does_not_extend_cooldown_for_later_relapse(self, alert_state_db):
        """Review finding, exercised end-to-end: a failed recovery-email send must not leave
        last_alerted_at bumped to "now" behind, or a later unpriced-model relapse on the same
        key gets silently blocked by a cooldown from an alert that was never actually sent.
        Sequence: alert on grok-4.6 -> recovery send fails (claim must be released, original
        last_alerted_at restored) -> model relapses to unpriced immediately -> with cooldown
        forced past, the relapse alert must go out (proves last_alerted_at wasn't left bumped
        by the failed recovery claim)."""
        _run_sync([_obs("o1", "grok-4.6", None)], alert_state_db)
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        _, mock_send_recovered = _run_sync(
            [_obs("o2", "grok-4.6", 1.0)], alert_state_db, recovered_send_succeeds=False
        )
        mock_send_recovered.assert_called_once()
        # Send failed, so the model must still show as alerted (claim was released).
        assert alert_state_db.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        # Cooldown forced to effectively zero: if last_alerted_at were left bumped by the
        # failed recovery claim, this would still be blocked. It must not be.
        with patch("open_webui.models.billing_alert_state.BILLING_ALERT_COOLDOWN_SECONDS", -1):
            mock_send_unpriced, _ = _run_sync([_obs("o3", "grok-4.6", None)], alert_state_db)
            mock_send_unpriced.assert_called_once()
