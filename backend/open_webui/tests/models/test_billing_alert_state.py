"""Tests for models/billing_alert_state.py — admin alert dedup/cooldown state."""
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
    STATUS_RECOVERED,
)


def _simulate_concurrent_claims(alert_state, alert_type, key, n=5):
    """Call try_claim_alert n times as if n instances raced on the same key. Returns how many
    calls returned True - with real atomicity this must be exactly 1, never 0 or more than 1."""
    return sum(1 for _ in range(n) if alert_state.try_claim_alert(alert_type, key))


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


class TestShouldAlert:
    def test_true_when_no_prior_alert(self, alert_state):
        assert alert_state.should_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_false_immediately_after_recording_alert(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.should_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

    def test_true_once_cooldown_has_elapsed(self, alert_state, db_session):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        row = db_session.query(BillingAlertState).filter_by(
            alert_type=ALERT_TYPE_UNPRICED_MODEL, key="grok-4.6"
        ).first()
        row.last_alerted_at = int(time.time()) - 999_999
        db_session.commit()
        assert alert_state.should_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_independent_per_key(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.should_alert(ALERT_TYPE_UNPRICED_MODEL, "qwen2.5:7b") is True

    def test_independent_per_alert_type(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.should_alert(ALERT_TYPE_ECB_UNREACHABLE, "grok-4.6") is True


class TestRecordAlerted:
    def test_sets_status_alerted(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_re_recording_updates_last_alerted_at(self, alert_state, db_session):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        row = db_session.query(BillingAlertState).filter_by(
            alert_type=ALERT_TYPE_UNPRICED_MODEL, key="grok-4.6"
        ).first()
        row.last_alerted_at = 0
        db_session.commit()

        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        row = db_session.query(BillingAlertState).filter_by(
            alert_type=ALERT_TYPE_UNPRICED_MODEL, key="grok-4.6"
        ).first()
        assert row.last_alerted_at > 0

    def test_relapse_after_recovery_re_arms_status(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.record_recovered(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True


class TestGetAlertedKeys:
    def test_returns_only_alerted_status_keys(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "qwen2.5:7b")
        alert_state.record_recovered(ALERT_TYPE_UNPRICED_MODEL, "qwen2.5:7b")

        assert alert_state.get_alerted_keys(ALERT_TYPE_UNPRICED_MODEL) == {"grok-4.6"}

    def test_empty_when_nothing_alerted(self, alert_state):
        assert alert_state.get_alerted_keys(ALERT_TYPE_UNPRICED_MODEL) == set()

    def test_scoped_to_alert_type(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_ECB_UNREACHABLE, "__ecb__")
        assert alert_state.get_alerted_keys(ALERT_TYPE_UNPRICED_MODEL) == set()


class TestRecordRecovered:
    def test_marks_status_recovered(self, alert_state, db_session):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.record_recovered(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        row = db_session.query(BillingAlertState).filter_by(
            alert_type=ALERT_TYPE_UNPRICED_MODEL, key="grok-4.6"
        ).first()
        assert row.status == STATUS_RECOVERED

    def test_noop_when_no_prior_row(self, alert_state):
        # Should not raise even though no row exists yet for this key.
        alert_state.record_recovered(ALERT_TYPE_UNPRICED_MODEL, "never-alerted")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "never-alerted") is False


class TestTryClaimAlert:
    """Copilot review finding: should_alert() + send + record_alerted() is a check-then-act
    race - two callers can both see should_alert()==True before either records anything.
    try_claim_alert() closes that by making the check-and-write a single atomic operation."""

    def test_true_on_first_claim_no_prior_row(self, alert_state):
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_false_on_immediate_second_claim(self, alert_state):
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

    def test_only_one_winner_across_simulated_concurrent_callers(self, alert_state):
        wins = _simulate_concurrent_claims(alert_state, ALERT_TYPE_UNPRICED_MODEL, "grok-4.6", n=10)
        assert wins == 1

    def test_true_again_once_cooldown_has_elapsed(self, alert_state, db_session):
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        row = db_session.query(BillingAlertState).filter_by(
            alert_type=ALERT_TYPE_UNPRICED_MODEL, key="grok-4.6"
        ).first()
        row.last_alerted_at = int(time.time()) - 999_999
        db_session.commit()
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_independent_per_key(self, alert_state):
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "qwen2.5:7b") is True


class TestReleaseClaim:
    def test_release_lets_next_claim_succeed_immediately(self, alert_state):
        alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

        alert_state.release_claim(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_release_of_nonexistent_row_does_not_raise(self, alert_state):
        alert_state.release_claim(ALERT_TYPE_UNPRICED_MODEL, "never-claimed")


class TestTryClaimRecovery:
    def test_true_when_currently_alerted(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

    def test_false_when_already_recovered(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

    def test_false_when_never_alerted(self, alert_state):
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "never-alerted") is False

    def test_only_one_winner_across_simulated_concurrent_callers(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        wins = sum(
            1 for _ in range(10)
            if alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        )
        assert wins == 1


class TestReleaseRecoveryClaim:
    def test_release_reverts_to_alerted_and_allows_reclaim(self, alert_state):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is False

        alert_state.release_recovery_claim(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        assert alert_state.is_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True
        assert alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6") is True

    def test_release_does_not_delete_row(self, alert_state, db_session):
        alert_state.record_alerted(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")
        alert_state.release_recovery_claim(ALERT_TYPE_UNPRICED_MODEL, "grok-4.6")

        row = db_session.query(BillingAlertState).filter_by(
            alert_type=ALERT_TYPE_UNPRICED_MODEL, key="grok-4.6"
        ).first()
        assert row is not None
        assert row.status == STATUS_ALERTED
