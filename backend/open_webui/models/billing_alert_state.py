import logging
import time

from sqlalchemy import BigInteger, Column, Text, UniqueConstraint

from open_webui.env import BILLING_ALERT_COOLDOWN_SECONDS
from open_webui.internal.db import Base, get_db

log = logging.getLogger(__name__)


####################
# BillingAlertState DB Schema
####################


ALERT_TYPE_UNPRICED_MODEL = 'unpriced_model'
ALERT_TYPE_ECB_UNREACHABLE = 'ecb_unreachable'

STATUS_ALERTED = 'alerted'
STATUS_RECOVERED = 'recovered'

# Fixed key for the single ECB-unreachable alert (not per-model).
ECB_ALERT_KEY = '__ecb__'


class BillingAlertState(Base):
    __tablename__ = 'billing_alert_state'

    id = Column(Text, primary_key=True, unique=True)
    alert_type = Column(Text, nullable=False)
    key = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    last_alerted_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint('alert_type', 'key', name='uq_billing_alert_state_type_key'),)


####################
# Table accessor
####################


class BillingAlertStateTable:
    def _row_id(self, alert_type: str, key: str) -> str:
        return f'{alert_type}:{key}'

    def should_alert(self, alert_type: str, key: str) -> bool:
        """True if an alert for this (alert_type, key) should be sent now: no prior alert,
        or the cooldown window has elapsed since the last one. Does not record anything -
        call record_alerted() after a successful send."""
        with get_db() as db:
            row = (
                db.query(BillingAlertState)
                .filter_by(alert_type=alert_type, key=key)
                .first()
            )
            if row is None:
                return True
            return (int(time.time()) - row.last_alerted_at) > BILLING_ALERT_COOLDOWN_SECONDS

    def record_alerted(self, alert_type: str, key: str) -> None:
        """Upsert the row to status=alerted with last_alerted_at=now, after a successful send."""
        now_ts = int(time.time())
        with get_db() as db:
            row = (
                db.query(BillingAlertState)
                .filter_by(alert_type=alert_type, key=key)
                .first()
            )
            if row:
                row.status = STATUS_ALERTED
                row.last_alerted_at = now_ts
            else:
                db.add(
                    BillingAlertState(
                        id=self._row_id(alert_type, key),
                        alert_type=alert_type,
                        key=key,
                        status=STATUS_ALERTED,
                        last_alerted_at=now_ts,
                        created_at=now_ts,
                    )
                )
            db.commit()

    def is_alerted(self, alert_type: str, key: str) -> bool:
        """True if this (alert_type, key) currently has an unresolved (status=alerted) row."""
        with get_db() as db:
            row = (
                db.query(BillingAlertState)
                .filter_by(alert_type=alert_type, key=key, status=STATUS_ALERTED)
                .first()
            )
            return row is not None

    def get_alerted_keys(self, alert_type: str) -> set[str]:
        """All keys currently in status=alerted for this alert_type."""
        with get_db() as db:
            rows = (
                db.query(BillingAlertState.key)
                .filter_by(alert_type=alert_type, status=STATUS_ALERTED)
                .all()
            )
            return {r[0] for r in rows}

    def record_recovered(self, alert_type: str, key: str) -> None:
        """Mark a previously-alerted (alert_type, key) as recovered, after a successful send."""
        with get_db() as db:
            row = (
                db.query(BillingAlertState)
                .filter_by(alert_type=alert_type, key=key)
                .first()
            )
            if row:
                row.status = STATUS_RECOVERED
                db.commit()


BillingAlertStateDB = BillingAlertStateTable()
