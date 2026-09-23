"""Tests for models/billing_alert_state.py — admin alert dedup/cooldown/claim state."""
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from open_webui.models.billing_alert_state import (
    ALERT_TYPE_ECB_UNREACHABLE,
    ALERT_TYPE_UNPRICED_MODEL,
    BillingAlertState,
    BillingAlertStateTable,
    STATUS_ALERTED,
    STATUS_CLAIMING,
    STATUS_RECOVERED,
    STATUS_RECOVERING,
)


def _simulate_concurrent_claims(alert_state, alert_type, key, n=5):
    """Call try_claim_alert n times as if n instances raced on the same key. Returns how many
    calls returned True - with real atomicity this must be exactly 1, never 0 or more than 1."""
    return sum(1 for _ in range(n) if alert_state.try_claim_alert(alert_type, key))


def _seed_alerted(alert_state, alert_type, key):
    """Test-only setup helper: get a row into a confirmed status=alerted state via the real
    production claim/confirm path (try_claim_alert + confirm_alert), rather than a dedicated
    "just set status=alerted" method - there is no such method in production code, since
    every real call site goes through claim-then-confirm."""
    alert_state.try_claim_alert(alert_type, key)
    alert_state.confirm_alert(alert_type, key)


@pytest.fixture(scope="module")
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    # Create only the billing_alert_state table — avoids FK errors from unrelated tables
    BillingAlertState.__table__.create(engine, checkfirst=True)
    yield engine
    BillingAlertState.__table__.drop(engine)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.query(BillingAlertState).delete()
    session.commit()
    session.close()


@pytest.fixture
def alert_state(db_session):
    """BillingAlertStateTable instance with patched get_db pointing to in-memory session."""
    @contextmanager
    def _get_db():
        yield db_session

    with patch("open_webui.models.billing_alert_state.get_db", _get_db):
        yield BillingAlertStateTable()


def _row(db_session, alert_type, key):
    return db_session.query(BillingAlertState).filter_by(alert_type=alert_type, key=key).first()


class TestTryClaimAlert:
    """Copilot review finding: should_alert() + send + record_alerted() (the original,
    now-removed design) was a check-then-act race - two callers could both see "due" before
    either recorded anything. try_claim_alert() closes that by making the check-and-write a
    single atomic operation.

    try_claim_alert() is claim-only: it writes status=claiming, not status=alerted, and does
    NOT touch last_alerted_at. confirm_alert() must be called after a successful send to
    actually start the cooldown - see TestConfirmAlert."""

    def test_true_on_first_claim_no_prior_row(self, alert_state, db_session):
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row.status == STATUS_CLAIMING
        assert row.last_alerted_at == 0  # not yet confirmed

    def test_false_on_immediate_second_claim(self, alert_state):
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

    def test_only_one_winner_across_simulated_concurrent_callers(self, alert_state):
        wins = _simulate_concurrent_claims(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6", n=10)
        assert wins == 1

    def test_true_again_once_cooldown_has_elapsed_after_confirm(self, alert_state, db_session):
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.confirm_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        row.last_alerted_at = int(time.time()) - 999_999
        db_session.commit()
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_independent_per_key(self, alert_state):
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "qwen2.5:7b") is True

    def test_independent_per_alert_type(self, alert_state):
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_alert(ALERT_TYPE_ECB_UNREACHABLE, "grok-4.6") is True

    def test_orphaned_claim_reclaimable_after_ttl(self, alert_state, db_session):
        """Review finding: a process crash/restart between try_claim_alert() and the send
        completing skips release_claim() entirely (no exception runs). Without a TTL, that
        orphaned status=claiming row would block a real alert for the full
        BILLING_ALERT_COOLDOWN_SECONDS despite no email ever having been sent. It must
        instead become reclaimable after BILLING_ALERT_CLAIM_TTL_SECONDS."""
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        # Still fresh - must not be reclaimable yet.
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row.status == STATUS_CLAIMING
        row.claimed_at = int(time.time()) - 999_999
        db_session.commit()

        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_fresh_claim_not_reclaimable_within_ttl(self, alert_state):
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

    def test_recovered_model_relapse_reclaimable_after_cooldown(self, alert_state, db_session):
        """Bug found while adding claim-TTL handling: the reclaim WHERE clause only checked
        status=alerted (cooldown) or status=claiming (TTL) - it never accounted for
        status=recovered, which is what a model sits at after a confirmed recovery. A relapse
        to unpriced on a status=recovered model could never be reclaimed at all, regardless
        of how much time passed, silently breaking every relapse alert."""
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        previous_last_alerted_at = alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.confirm_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert previous_last_alerted_at is not None

        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row.status == STATUS_RECOVERED
        row.last_alerted_at = int(time.time()) - 999_999
        db_session.commit()

        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True


class TestConfirmAlert:
    def test_flips_claiming_to_alerted_and_sets_last_alerted_at(self, alert_state, db_session):
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        before = int(time.time())
        alert_state.confirm_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row.status == STATUS_ALERTED
        assert row.last_alerted_at >= before

    def test_confirmed_alert_blocks_reclaim_within_cooldown(self, alert_state):
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.confirm_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

    def test_noop_if_row_not_in_claiming_status(self, alert_state, db_session):
        # Confirming a key with no claim at all should not create a bogus row.
        alert_state.confirm_alert(ALERT_TYPE_UNPRICED_MODEL, "never-claimed")
        assert _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "never-claimed") is None


class TestReleaseClaim:
    def test_release_lets_next_claim_succeed_immediately(self, alert_state):
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

        alert_state.release_claim(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_release_of_nonexistent_row_does_not_raise(self, alert_state):
        alert_state.release_claim(ALERT_TYPE_UNPRICED_MODEL, "never-claimed")


class TestIsAlertedAndGetAlertedKeys:
    def test_true_for_confirmed_alert(self, alert_state):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_false_when_never_alerted(self, alert_state):
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "never-alerted") is False

    def test_false_after_confirmed_recovery(self, alert_state):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.confirm_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

    def test_relapse_after_recovery_re_arms_alerted_once_cooldown_elapses(self, alert_state, db_session):
        """A relapse to unpriced right after a confirmed recovery is cooldown-gated, same as
        any other repeat alert (see test_relapse_immediately_after_recovery_is_still_cooldown_gated
        in test_billing_alert_dispatch.py) - it only re-arms once
        BILLING_ALERT_COOLDOWN_SECONDS has elapsed since the confirmed recovery."""
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.confirm_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        row.last_alerted_at = int(time.time()) - 999_999
        db_session.commit()

        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_get_alerted_keys_returns_only_alerted_status_keys(self, alert_state):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "qwen2.5:7b")
        alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "qwen2.5:7b")
        alert_state.confirm_recovery(ALERT_TYPE_UNPRICED_MODEL, "qwen2.5:7b")

        assert alert_state.get_alerted_keys(ALERT_TYPE_UNPRICED_MODEL) == {"grok-4.6"}

    def test_get_alerted_keys_empty_when_nothing_alerted(self, alert_state):
        assert alert_state.get_alerted_keys(ALERT_TYPE_UNPRICED_MODEL) == set()

    def test_get_alerted_keys_scoped_to_alert_type(self, alert_state):
        _seed_alerted(alert_state, ALERT_TYPE_ECB_UNREACHABLE, "__ecb__")
        assert alert_state.get_alerted_keys(ALERT_TYPE_UNPRICED_MODEL) == set()


class TestTryClaimRecovery:
    """Mirrors TestTryClaimAlert: claim-only, writes status=recovering, does not touch
    last_alerted_at or flip to status=recovered until confirm_recovery() runs."""

    def test_returns_previous_last_alerted_at_when_currently_alerted(self, alert_state, db_session):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        before = int(time.time())
        result = alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert result is not None
        assert abs(result - before) <= 1  # confirm_alert() just set it to "now"

        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row.status == STATUS_RECOVERING  # claim-only, not yet confirmed
        # is_alerted()/get_alerted_keys() still treat a fresh (non-orphaned) recovering claim
        # as alerted - the recovery isn't real until confirm_recovery() runs.
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_none_when_already_recovering(self, alert_state):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is not None
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is None

    def test_none_when_never_alerted(self, alert_state):
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "never-alerted") is None

    def test_only_one_winner_across_simulated_concurrent_callers(self, alert_state):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        wins = sum(
            1 for _ in range(10)
            if alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is not None
        )
        assert wins == 1

    def test_orphaned_recovery_claim_reclaimable_after_ttl(self, alert_state, db_session):
        """Same crash-window gap as try_claim_alert(), for the recovery transition: a process
        crash between try_claim_recovery() and the send completing skips
        release_recovery_claim() entirely. The orphaned status=recovering row must become
        reclaimable after BILLING_ALERT_CLAIM_TTL_SECONDS, not stay stuck forever."""
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is None

        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row.status == STATUS_RECOVERING
        row.claimed_at = int(time.time()) - 999_999
        db_session.commit()

        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is not None

    def test_orphaned_recovery_claim_still_counted_as_alerted_key(self, alert_state, db_session):
        """An orphaned (but not yet TTL-expired) status=recovering row must keep showing up
        in get_alerted_keys() - otherwise it silently vanishes from the recovery-candidate
        scan the moment the claim was taken, whether or not the send ever completed."""
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert "grok-4.6" in alert_state.get_alerted_keys(ALERT_TYPE_UNPRICED_MODEL)


class TestConfirmRecovery:
    def test_flips_recovering_to_recovered_and_sets_last_alerted_at(self, alert_state, db_session):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        before = int(time.time())
        alert_state.confirm_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row.status == STATUS_RECOVERED
        assert row.last_alerted_at >= before
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False


class TestReleaseRecoveryClaim:
    def test_release_reverts_to_alerted_and_allows_reclaim(self, alert_state):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        previous_last_alerted_at = alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        alert_state.release_recovery_claim(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6", previous_last_alerted_at)
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is not None

    def test_release_does_not_delete_row(self, alert_state, db_session):
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        previous_last_alerted_at = alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.release_recovery_claim(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6", previous_last_alerted_at)

        row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row is not None
        assert row.status == STATUS_ALERTED

    def test_release_restores_original_last_alerted_at(self, alert_state, db_session):
        """Review finding: try_claim_recovery() must not leave last_alerted_at silently
        altered on a failed send. Since try_claim_recovery() is claim-only (does not touch
        last_alerted_at at all, only confirm_recovery() does), this mostly documents that
        release_recovery_claim() correctly restores whatever value was passed in."""
        _seed_alerted(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        original_row = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        original_row.last_alerted_at = original_row.last_alerted_at - 12345
        db_session.commit()
        backdated_value = original_row.last_alerted_at

        previous_last_alerted_at = alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert previous_last_alerted_at == backdated_value

        row_mid_claim = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row_mid_claim.last_alerted_at == backdated_value  # claim-only: untouched so far

        alert_state.release_recovery_claim(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6", previous_last_alerted_at)

        row_after_release = _row(db_session, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert row_after_release.last_alerted_at == backdated_value
