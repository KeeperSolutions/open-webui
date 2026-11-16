"""Check that fetched Langfuse observation ids match the ledger rows already stored for the same window.

The ledger skips a row whose observation id it already holds. If Langfuse returns different ids for
observations the ledger already contains, those rows would be inserted a second time and billed twice.
Before a set of observations is returned for insertion, the ids of the ledger rows in the query window are
compared with the fetched ids. When too few of the ledger rows are found among the fetched ids, nothing is
inserted and the sync fails. The ledger is not modified, so the next poll reads the same window again.
"""

import datetime as dt
import logging
import os
import threading
from typing import Iterable, Set

from open_webui.internal.db import get_db
from open_webui.models.usage_ledger import UsageLedger

log = logging.getLogger(__name__)

# The check applies only when the ledger holds at least this many rows for the window, so a few
# missing observations in a quiet window do not stop a sync.
_DEFAULT_MIN_ROWS = 20
# Smallest share of those ledger rows that must be found among the fetched ids.
_DEFAULT_MIN_RATIO = 0.8
# Ledger rows newer than this are not compared: an observation stored a moment ago may not be visible
# in the v2 API yet (up to about 15 minutes for langfuse-python before 4.7.0).
_SETTLE = dt.timedelta(minutes=15)

_state_lock = threading.Lock()
_trips_total = 0
_warned_values: Set[str] = set()


class LedgerIdMismatch(RuntimeError):
    """The fetched observation ids do not match the ledger rows stored for the same window."""


def _warn_invalid(name: str, raw: str, default) -> None:
    key = f"{name}={raw}"
    with _state_lock:
        if key in _warned_values:
            return
        _warned_values.add(key)
    log.warning("[ledger-poller] %s=%r is not valid; using %s.", name, raw, default)


def _min_rows() -> int:
    """LEDGER_ID_GUARD_MIN_ROWS, or the default when unset or not a positive integer."""
    raw = os.environ.get("LEDGER_ID_GUARD_MIN_ROWS")
    if raw is None or not raw.strip():
        return _DEFAULT_MIN_ROWS
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value >= 1:
        return value
    _warn_invalid("LEDGER_ID_GUARD_MIN_ROWS", raw, _DEFAULT_MIN_ROWS)
    return _DEFAULT_MIN_ROWS


def _min_ratio() -> float:
    """LEDGER_ID_GUARD_MIN_RATIO, or the default when unset or not a number above 0 and up to 1."""
    raw = os.environ.get("LEDGER_ID_GUARD_MIN_RATIO")
    if raw is None or not raw.strip():
        return _DEFAULT_MIN_RATIO
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if 0.0 < value <= 1.0:
        return value
    _warn_invalid("LEDGER_ID_GUARD_MIN_RATIO", raw, _DEFAULT_MIN_RATIO)
    return _DEFAULT_MIN_RATIO


def ledger_ids_between(start_epoch: int, end_epoch: int) -> Set[str]:
    """Observation ids of ledger rows with start_epoch <= observed_at < end_epoch."""
    with get_db() as db:
        rows = (
            db.query(UsageLedger.langfuse_observation_id)
            .filter(UsageLedger.observed_at >= start_epoch, UsageLedger.observed_at < end_epoch)
            .all()
        )
    return {row[0] for row in rows}


def check_ledger_overlap(fetched_ids: Iterable[str], since: dt.datetime, until: dt.datetime) -> None:
    """Raise LedgerIdMismatch when the ledger rows for the window are mostly absent from the fetched ids.

    The window is [since, until - 15 minutes). The check does nothing when the ledger holds fewer than
    the minimum number of rows for it. A failed ledger read raises, so an unverified set is never inserted.
    The error log contains counts and the window only.
    """
    start = int(since.timestamp())
    end = int((until - _SETTLE).timestamp())
    if end <= start:
        return

    ledger_ids = ledger_ids_between(start, end)
    minimum_rows = _min_rows()
    if len(ledger_ids) < minimum_rows:
        return

    fetched = set(fetched_ids)
    matched = len(ledger_ids & fetched)
    ratio = matched / len(ledger_ids)
    minimum_ratio = _min_ratio()
    if ratio >= minimum_ratio:
        return

    global _trips_total
    with _state_lock:
        _trips_total += 1
        trips = _trips_total
    window = (
        f"{dt.datetime.fromtimestamp(start, tz=dt.timezone.utc).isoformat()}"
        f" to {dt.datetime.fromtimestamp(end, tz=dt.timezone.utc).isoformat()}"
    )
    message = (
        f"{len(ledger_ids) - matched} of {len(ledger_ids)} ledger rows in the window {window} were not returned "
        f"by Langfuse ({len(fetched)} observations fetched, {matched} matched, {ratio:.0%} of ledger rows; "
        f"minimum {minimum_ratio:.0%} from {minimum_rows} rows)"
    )
    log.error("[ledger-poller] id guard: %s; nothing is inserted (occurrences since start: %d)", message, trips)
    raise LedgerIdMismatch(f"Ledger id guard: {message}")
