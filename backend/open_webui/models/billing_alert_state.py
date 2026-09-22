import logging
import time

from sqlalchemy import BigInteger, Column, Text, UniqueConstraint, insert
from sqlalchemy.exc import IntegrityError

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
        """True if an alert for this (alert_type, key) would be due right now: no prior
        alert, or the cooldown window has elapsed since the last one. Advisory only - this
        is a plain read with no locking, so it must not gate a send on its own (two instances
        can both see True before either records anything). Use try_claim_alert() to actually
        reserve the right to send."""
        with get_db() as db:
            row = (
                db.query(BillingAlertState)
                .filter_by(alert_type=alert_type, key=key)
                .first()
            )
            if row is None:
                return True
            return (int(time.time()) - row.last_alerted_at) > BILLING_ALERT_COOLDOWN_SECONDS

    def try_claim_alert(self, alert_type: str, key: str) -> bool:
        """Atomically claim the right to send an alert for (alert_type, key): True if this
        call won the claim (no prior row, or the cooldown had elapsed), False if another
        caller already holds a live claim. Unlike should_alert(), the row is written as part
        of the same operation that decides the outcome, so two callers racing on the same key
        (e.g. two Cloud Run instances polling at once) cannot both win - only one INSERT or
        conditional UPDATE can succeed against the unique (alert_type, key) constraint /
        cooldown WHERE clause. Call this immediately before sending, not should_alert(); only
        proceed with the send if it returns True."""
        now_ts = int(time.time())
        row_id = self._row_id(alert_type, key)

        with get_db() as db:
            try:
                # Core insert() rather than db.add(BillingAlertState(...)) - avoids leaving a
                # half-registered ORM instance in the session's identity map after the
                # IntegrityError/rollback below, which previously triggered a spurious
                # SAWarning on a later claim attempt against the same (alert_type, key).
                db.execute(
                    insert(BillingAlertState).values(
                        id=row_id,
                        alert_type=alert_type,
                        key=key,
                        status=STATUS_ALERTED,
                        last_alerted_at=now_ts,
                        created_at=now_ts,
                    )
                )
                db.commit()
                return True
            except IntegrityError:
                # Row already exists (another caller created it, possibly just now) - fall
                # through to a conditional update instead of treating this as a hard failure.
                db.rollback()

            cutoff = now_ts - BILLING_ALERT_COOLDOWN_SECONDS
            result = db.execute(
                BillingAlertState.__table__.update()
                .where(
                    BillingAlertState.alert_type == alert_type,
                    BillingAlertState.key == key,
                    BillingAlertState.last_alerted_at <= cutoff,
                )
                .values(status=STATUS_ALERTED, last_alerted_at=now_ts)
            )
            db.commit()
            return result.rowcount > 0

    def release_claim(self, alert_type: str, key: str) -> None:
        """Undo a try_claim_alert() whose send failed, by deleting the row - restores "no
        prior alert" so the next poll can retry immediately instead of waiting out the
        cooldown. Safe to call even if another caller has since claimed/re-claimed the same
        key (e.g. after this instance's own claim already expired) - it only ever deletes the
        current row for (alert_type, key), whatever state it's in, so at worst it makes the
        next poll retry slightly earlier than strictly necessary, never later."""
        with get_db() as db:
            db.query(BillingAlertState).filter_by(alert_type=alert_type, key=key).delete()
            db.commit()

    def record_alerted(self, alert_type: str, key: str) -> None:
        """Upsert the row to status=alerted with last_alerted_at=now, after a successful send.
        Not race-safe on its own - prefer try_claim_alert() for the send-gating path. Kept for
        callers that already know they hold the claim (e.g. recording a status change without
        re-deciding whether to send)."""
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

    def try_claim_recovery(self, alert_type: str, key: str) -> bool:
        """Atomically claim the right to send a "pricing recovered" alert for (alert_type,
        key): True if this call flipped it from alerted to recovered, False if it was already
        recovered (or never alerted) by the time this ran - e.g. another instance's poll got
        there first. Same race this is meant to close as try_claim_alert(), but for the
        recovery transition: computing "is this model newly recovered" and sending the email
        are two different callers' concern, this method just makes the state flip itself
        atomic so at most one caller sees True for a given claim."""
        now_ts = int(time.time())
        with get_db() as db:
            result = db.execute(
                BillingAlertState.__table__.update()
                .where(
                    BillingAlertState.alert_type == alert_type,
                    BillingAlertState.key == key,
                    BillingAlertState.status == STATUS_ALERTED,
                )
                .values(status=STATUS_RECOVERED, last_alerted_at=now_ts)
            )
            db.commit()
            return result.rowcount > 0

    def release_recovery_claim(self, alert_type: str, key: str) -> None:
        """Undo a try_claim_recovery() whose send failed, by reverting status back to
        alerted - restores the pre-claim state so the next poll can retry. Unlike
        release_claim(), this does not delete the row: an alerted-but-not-yet-recovered
        model still needs its cooldown/history preserved, not reset to "never alerted"."""
        with get_db() as db:
            db.query(BillingAlertState).filter_by(
                alert_type=alert_type, key=key, status=STATUS_RECOVERED
            ).update({'status': STATUS_ALERTED})
            db.commit()

    def record_recovered(self, alert_type: str, key: str) -> None:
        """Mark a previously-alerted (alert_type, key) as recovered, after a successful send.
        Not race-safe on its own - prefer try_claim_recovery() for the send-gating path."""
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
