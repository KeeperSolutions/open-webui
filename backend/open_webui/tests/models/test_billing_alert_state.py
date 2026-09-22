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
