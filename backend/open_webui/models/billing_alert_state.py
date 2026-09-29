import logging
import time
from typing import Optional

from sqlalchemy import BigInteger, Column, Text, UniqueConstraint, insert
from sqlalchemy.exc import IntegrityError

from open_webui.env import BILLING_ALERT_CLAIM_TTL_SECONDS, BILLING_ALERT_COOLDOWN_SECONDS
from open_webui.internal.db import Base, get_db

log = logging.getLogger(__name__)


####################
# BillingAlertState DB Schema
####################


ALERT_TYPE_UNPRICED_MODEL = 'unpriced_model'
ALERT_TYPE_ECB_UNREACHABLE = 'ecb_unreachable'

# In-flight: try_claim_alert() wrote this, send not yet confirmed. last_alerted_at is NOT
# updated for this status - only confirm_alert() touches it, so an abandoned claim (crash
# between claim and send, no release_claim() ever runs) can't leave a stale bump behind for
# the real cooldown to read.
STATUS_CLAIMING = 'claiming'
STATUS_ALERTED = 'alerted'
# In-flight: try_claim_recovery() wrote this, send not yet confirmed. Same reasoning as
# STATUS_CLAIMING, but for the alerted -> recovered transition - status only actually becomes
# STATUS_RECOVERED once confirm_recovery() runs after a successful send.
STATUS_RECOVERING = 'recovering'
STATUS_RECOVERED = 'recovered'

# Fixed key for the single ECB-unreachable alert (not per-model).
ECB_ALERT_KEY = '__ecb__'


class BillingAlertState(Base):
    __tablename__ = 'billing_alert_state'

    id = Column(Text, primary_key=True, unique=True)
    alert_type = Column(Text, nullable=False)
    key = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    # Set only by a confirmed alert (confirm_alert() / confirm_recovery()). A row sitting in
    # status=claiming or status=recovering has NOT touched this yet - it still reflects
    # whatever the last confirmed alert was (or is unset/0 if this is the row's first-ever
    # claim).
    last_alerted_at = Column(BigInteger, nullable=False)
    # When the current status=claiming/status=recovering attempt started; used only to detect
    # an orphaned claim past BILLING_ALERT_CLAIM_TTL_SECONDS. Irrelevant once status leaves
    # either in-flight state.
    claimed_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint('alert_type', 'key', name='uq_billing_alert_state_type_key'),)


####################
# Table accessor
####################


class BillingAlertStateTable:
    def _row_id(self, alert_type: str, key: str) -> str:
        return f'{alert_type}:{key}'

    def try_claim_alert(self, alert_type: str, key: str) -> bool:
        """Atomically claim the right to send an alert for (alert_type, key): True if this
        call won the claim (no prior row, the cooldown had elapsed, or a prior claim was
        orphaned past BILLING_ALERT_CLAIM_TTL_SECONDS), False if another caller already holds
        a live claim or cooldown. The row is written as part of the
        same operation that decides the outcome, so two callers racing on the same key (e.g.
        two Cloud Run instances polling at once) cannot both win - only one INSERT or
        conditional UPDATE can succeed against the unique (alert_type, key) constraint /
        cooldown-or-orphaned-claim WHERE clause.

        This only writes status=claiming and claimed_at=now - it deliberately does NOT touch
        last_alerted_at. That's set only by confirm_alert(), after the send actually
        succeeds. If the process crashes/restarts between this call and confirm_alert() (no
        exception runs, so release_claim() never fires), the claim just sits at
        status=claiming with no cooldown-relevant side effect; once BILLING_ALERT_CLAIM_TTL_SECONDS
        passes, the WHERE clause below treats it as orphaned and lets a later poll reclaim it,
        instead of it blocking a real alert for the full BILLING_ALERT_COOLDOWN_SECONDS.

        Call this immediately before sending. Only proceed with the send if it returns True,
        and call confirm_alert() after a successful send (or release_claim() after a failed
        one)."""
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
                        status=STATUS_CLAIMING,
                        last_alerted_at=0,
                        claimed_at=now_ts,
                        created_at=now_ts,
                    )
                )
                db.commit()
                return True
            except IntegrityError:
                # Row already exists (another caller created it, possibly just now) - fall
                # through to a conditional update instead of treating this as a hard failure.
                db.rollback()

            cooldown_cutoff = now_ts - BILLING_ALERT_COOLDOWN_SECONDS
            claim_ttl_cutoff = now_ts - BILLING_ALERT_CLAIM_TTL_SECONDS
            result = db.execute(
                BillingAlertState.__table__.update()
                .where(
                    BillingAlertState.alert_type == alert_type,
                    BillingAlertState.key == key,
                    (
                        # Confirmed alert (still outstanding, or already confirmed
                        # recovered - either way, last_alerted_at is this key's real clock)
                        # whose cooldown has elapsed.
                        (BillingAlertState.status.in_((STATUS_ALERTED, STATUS_RECOVERED)))
                        & (BillingAlertState.last_alerted_at <= cooldown_cutoff)
                    )
                    | (
                        # Or an abandoned in-flight claim, past its short TTL.
                        (BillingAlertState.status.in_((STATUS_CLAIMING, STATUS_RECOVERING)))
                        & (BillingAlertState.claimed_at <= claim_ttl_cutoff)
                    ),
                )
                .values(status=STATUS_CLAIMING, claimed_at=now_ts)
            )
            db.commit()
            return result.rowcount > 0

    def confirm_alert(self, alert_type: str, key: str) -> None:
        """Confirm a try_claim_alert() whose send succeeded: flips status=claiming ->
        alerted and sets last_alerted_at=now, starting this key's real cooldown. This is the
        only path (besides try_claim_recovery()'s winning branch) that writes
        last_alerted_at - a claim alone never does, so an orphaned/abandoned claim can't
        silently extend the cooldown for an alert that was never actually sent."""
        now_ts = int(time.time())
        with get_db() as db:
            db.query(BillingAlertState).filter_by(
                alert_type=alert_type, key=key, status=STATUS_CLAIMING
            ).update({'status': STATUS_ALERTED, 'last_alerted_at': now_ts})
            db.commit()

    def release_claim(self, alert_type: str, key: str) -> None:
        """Undo a try_claim_alert() whose send failed, by deleting the row - restores "no
        prior alert" so the next poll can retry immediately instead of waiting out the
        cooldown. Only reachable on an explicit in-process failure (an exception, or the send
        returning falsy); a hard crash/restart between claim and send skips this entirely,
        which is what BILLING_ALERT_CLAIM_TTL_SECONDS in try_claim_alert() is for. Safe to
        call even if another caller has since claimed/re-claimed the same key (e.g. after
        this instance's own claim already expired) - it only ever deletes the current row for
        (alert_type, key), whatever state it's in, so at worst it makes the next poll retry
        slightly earlier than strictly necessary, never later."""
        with get_db() as db:
            db.query(BillingAlertState).filter_by(alert_type=alert_type, key=key).delete()
            db.commit()

    def _effectively_alerted_filter(self):
        """status=alerted, OR status=recovering (fresh or orphaned) - a recovering row means
        "was alerted, recovery not yet confirmed sent", so it must count as alerted whether
        the claim is still in-flight or was abandoned by a crash. This is deliberately NOT
        gated on the claim TTL: TTL only controls whether try_claim_recovery() may re-claim a
        stuck row (see its own WHERE clause), it does not change whether the row still
        represents an outstanding alert."""
        return (BillingAlertState.status == STATUS_ALERTED) | (
            BillingAlertState.status == STATUS_RECOVERING
        )

    def is_alerted(self, alert_type: str, key: str) -> bool:
        """True if this (alert_type, key) currently has an unresolved alert - status=alerted,
        or an orphaned (past-TTL) status=recovering claim, see _effectively_alerted_filter()."""
        with get_db() as db:
            row = (
                db.query(BillingAlertState)
                .filter(
                    BillingAlertState.alert_type == alert_type,
                    BillingAlertState.key == key,
                    self._effectively_alerted_filter(),
                )
                .first()
            )
            return row is not None

    def get_alerted_keys(self, alert_type: str) -> set[str]:
        """All keys currently unresolved for this alert_type - see is_alerted()/
        _effectively_alerted_filter()."""
        with get_db() as db:
            rows = (
                db.query(BillingAlertState.key)
                .filter(
                    BillingAlertState.alert_type == alert_type,
                    self._effectively_alerted_filter(),
                )
                .all()
            )
            return {r[0] for r in rows}

    def try_claim_recovery(self, alert_type: str, key: str) -> Optional[int]:
        """Atomically claim the right to send a "pricing recovered" alert for (alert_type,
        key): returns the row's last_alerted_at from immediately before this call if it won
        the claim (flipped alerted -> recovering), or None if another caller already holds a
        live claim/recovery or the key was never alerted. Same race this is meant to close as
        try_claim_alert(), but for the recovery transition: computing "is this model newly
        recovered" and sending the email are two different callers' concern, this method just
        makes the state flip itself atomic so at most one caller can win a given claim.

        This is claim-only, mirroring try_claim_alert(): it does NOT flip to
        status=recovered or touch last_alerted_at. Call confirm_recovery() after a successful
        send, or release_recovery_claim() after a failed one. If the process crashes/restarts
        between this call and confirm_recovery() (no exception, so release_recovery_claim()
        never fires), the row is left at status=recovering - is_alerted()/get_alerted_keys()
        always report a status=recovering row as alerted (see _effectively_alerted_filter()),
        so an abandoned claim doesn't silently drop the model from the next poll's recovery
        scan. What BILLING_ALERT_CLAIM_TTL_SECONDS controls is narrower: once it passes, this
        method's own WHERE clause below additionally allows a fresh caller to re-claim (and
        retry sending for) that same stuck row, instead of it staying claimed forever."""
        now_ts = int(time.time())
        claim_ttl_cutoff = now_ts - BILLING_ALERT_CLAIM_TTL_SECONDS
        with get_db() as db:
            row = (
                db.query(BillingAlertState)
                .filter(
                    BillingAlertState.alert_type == alert_type,
                    BillingAlertState.key == key,
                    self._effectively_alerted_filter(),
                )
                .first()
            )
            if row is None:
                return None
            previous_last_alerted_at = row.last_alerted_at

            result = db.execute(
                BillingAlertState.__table__.update()
                .where(
                    BillingAlertState.alert_type == alert_type,
                    BillingAlertState.key == key,
                    (BillingAlertState.status == STATUS_ALERTED)
                    | (
                        (BillingAlertState.status == STATUS_RECOVERING)
                        & (BillingAlertState.claimed_at <= claim_ttl_cutoff)
                    ),
                )
                .values(status=STATUS_RECOVERING, claimed_at=now_ts)
            )
            db.commit()
            return previous_last_alerted_at if result.rowcount > 0 else None

    def confirm_recovery(self, alert_type: str, key: str) -> None:
        """Confirm a try_claim_recovery() whose send succeeded: flips status=recovering ->
        recovered and sets last_alerted_at=now. Mirrors confirm_alert() - only a confirmed
        recovery touches last_alerted_at, so an orphaned recovery claim can't silently affect
        cooldown timing either."""
        now_ts = int(time.time())
        with get_db() as db:
            db.query(BillingAlertState).filter_by(
                alert_type=alert_type, key=key, status=STATUS_RECOVERING
            ).update({'status': STATUS_RECOVERED, 'last_alerted_at': now_ts})
            db.commit()

    def release_recovery_claim(self, alert_type: str, key: str, restore_last_alerted_at: int) -> None:
        """Undo a try_claim_recovery() whose send failed, by reverting status back to alerted
        AND restoring last_alerted_at to the value it held before the claim. Only reachable
        on an explicit in-process failure; a hard crash/restart skips this entirely, which is
        what BILLING_ALERT_CLAIM_TTL_SECONDS in try_claim_recovery() is for.
        restore_last_alerted_at must be the row's last_alerted_at from immediately before the
        matching try_claim_recovery() call. Unlike release_claim(), this does not delete the
        row: an alerted-but-not-yet-recovered model still needs its cooldown/history
        preserved, not reset to "never alerted"."""
        with get_db() as db:
            db.query(BillingAlertState).filter_by(
                alert_type=alert_type, key=key, status=STATUS_RECOVERING
            ).update({'status': STATUS_ALERTED, 'last_alerted_at': restore_last_alerted_at})
            db.commit()


BillingAlertStateDB = BillingAlertStateTable()
