import datetime as dt
import logging
import threading
import time
from typing import Generator, Dict, Any, List

import requests
from cachetools import TTLCache

from open_webui.langfuse import ledger_guard
from open_webui.langfuse.metrics import auth_header, load_env

log = logging.getLogger(__name__)

_PAGE_SIZE = 500
_OBSERVATIONS_PATH = "/api/public/v2/observations"
# Field groups the poller reads: core (id, traceId, startTime), basic (name, userId),
# model (model) and usage (usageDetails, totalCost).
_FIELDS = "core,basic,model,usage"
# toStartTime is exclusive, so it is set slightly ahead of now to tolerate clock skew.
_TO_START_TIME_MARGIN = dt.timedelta(seconds=60)
_USAGE_KEYS = ("input", "output", "total")
# Consecutive 429 responses for one request before the fetch gives up and raises.
_MAX_RATE_LIMIT_RETRIES = 5

# Observation ids already reported, so rows re-read by the overlapping poll window or the
# nightly rescan are reported once, not on every read. Counters are totals since process start.
_missing_user_id_seen: TTLCache[str, bool] = TTLCache(maxsize=10_000, ttl=7200)
_missing_user_id_total = 0
_bad_cost_seen: TTLCache[str, bool] = TTLCache(maxsize=10_000, ttl=7200)
_bad_cost_total = 0
_bad_start_time_seen: TTLCache[str, bool] = TTLCache(maxsize=10_000, ttl=7200)
_bad_start_time_total = 0
_rate_limit_giveups_total = 0
_usage_group_missing_total = 0
_state_lock = threading.Lock()

_fetch_stats = threading.local()


def last_fetch_count() -> int | None:
    """Number of observations returned by the last completed fetch in this thread, or None."""
    return getattr(_fetch_stats, "fetched", None)


def reset_fetch_stats() -> None:
    _fetch_stats.fetched = None


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _format_ts(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _first_sighting(cache: TTLCache, row: Dict[str, Any]) -> bool:
    """True the first time this row's id is seen; a row without a string id is always new."""
    obs_id = row.get("id")
    with _state_lock:
        if isinstance(obs_id, str):
            if obs_id in cache:
                return False
            cache[obs_id] = True
        return True


def _to_v1_shape(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map one v2 observation row to the field names the ledger sync reads.

    A key that is absent in the v2 row stays absent in the result, so a missing
    usage or cost is not turned into zero. totalCost is converted without rounding.
    userId is handled by _user_id_from_row.
    """
    out: Dict[str, Any] = {k: row[k] for k in ("id", "traceId", "name", "model", "startTime") if k in row}

    usage = row.get("usageDetails")
    if isinstance(usage, dict):
        out["usage"] = {k: usage[k] for k in _USAGE_KEYS if k in usage}

    if "totalCost" in row:
        cost = row["totalCost"]
        try:
            out["calculatedTotalCost"] = None if cost is None else float(cost)
        except (TypeError, ValueError):
            # Passed through unchanged; the ledger sync treats an unparseable cost as missing.
            out["calculatedTotalCost"] = cost
    return out


def _user_id_from_row(row: Dict[str, Any]) -> str:
    """Return the row's userId, or "" when it is missing, empty or not a string.

    A row without a userId is reported once per observation id with a warning and
    counted in _missing_user_id_total. The row is still returned and billed as before.
    """
    user_id = row.get("userId")
    if isinstance(user_id, str) and user_id:
        return user_id

    obs_id = row.get("id")
    global _missing_user_id_total
    if not _first_sighting(_missing_user_id_seen, row):
        return ""
    with _state_lock:
        _missing_user_id_total += 1
        total = _missing_user_id_total
    log.warning(
        "[ledger-poller] observation without userId (observation=%s, total without userId since start: %d)",
        obs_id,
        total,
    )
    return ""


def _cost_is_number(row: Dict[str, Any]) -> bool:
    """False only when totalCost is present, not null, and cannot be converted to a float."""
    cost = row.get("totalCost")
    if cost is None:
        return True
    try:
        float(cost)
        return True
    except (TypeError, ValueError):
        return False


def _start_time_parses(row: Dict[str, Any]) -> bool:
    """Same normalisation the ledger sync applies to startTime; a failure there stores the current time."""
    value = row.get("startTime")
    if not isinstance(value, str):
        return False
    try:
        dt.datetime.strptime(value.rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S")
        return True
    except ValueError:
        return False


def _report_unusable_values(rows: List[Dict[str, Any]]) -> None:
    """Warn, with counts only, about costs and start times the ledger sync silently replaces.

    A cost that is not a number is stored as no cost. A startTime that does not parse is stored
    as the current time. Neither value is changed here, so ledger rows are the same as without
    this report. Each observation is counted once while it stays in the seen-cache.
    """
    global _bad_cost_total, _bad_start_time_total
    new_bad_cost = sum(1 for r in rows if not _cost_is_number(r) and _first_sighting(_bad_cost_seen, r))
    new_bad_start = sum(1 for r in rows if not _start_time_parses(r) and _first_sighting(_bad_start_time_seen, r))
    with _state_lock:
        _bad_cost_total += new_bad_cost
        _bad_start_time_total += new_bad_start
        cost_total, start_total = _bad_cost_total, _bad_start_time_total
    if new_bad_cost:
        log.warning(
            "[ledger-poller] %d of %d observations have a cost that is not a number; "
            "they are stored without a cost (total since start: %d)",
            new_bad_cost,
            len(rows),
            cost_total,
        )
    if new_bad_start:
        log.warning(
            "[ledger-poller] %d of %d observations have a startTime that cannot be parsed; "
            "they are stored with the current time (total since start: %d)",
            new_bad_start,
            len(rows),
            start_total,
        )


def _check_usage_group(rows: List[Dict[str, Any]]) -> None:
    """Raise when a non-empty result carries neither usageDetails nor totalCost on any row.

    The usage field group is requested, so a response without those keys on every row means the
    group was not returned. Syncing it would store every observation with zero tokens and no cost.
    """
    global _usage_group_missing_total
    if rows and not any("usageDetails" in r or "totalCost" in r for r in rows):
        with _state_lock:
            _usage_group_missing_total += 1
            total = _usage_group_missing_total
        log.error(
            "[ledger-poller] v2 response has no usage fields on any of %d observations (occurrences since start: %d)",
            len(rows),
            total,
        )
        raise RuntimeError(f"Langfuse v2 response has no usage fields on any of {len(rows)} observations")


def _fetch_raw_rows(host: str, headers: Dict[str, str], base_params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the raw v2 rows of every page, following meta.cursor until the response has none."""
    global _rate_limit_giveups_total
    rows: List[Dict[str, Any]] = []
    page = 1
    cursor = None
    used_cursors: set = set()
    rate_limited = 0
    while True:
        params = dict(base_params)
        if cursor is not None:
            params["cursor"] = cursor
        try:
            resp = requests.get(
                f"{host}{_OBSERVATIONS_PATH}",
                headers=headers,
                params=params,
                timeout=30,
            )
            if resp.status_code == 429:
                rate_limited += 1
                if rate_limited > _MAX_RATE_LIMIT_RETRIES:
                    with _state_lock:
                        _rate_limit_giveups_total += 1
                        giveups = _rate_limit_giveups_total
                    log.error(
                        "Langfuse rate limit: giving up on page %d after %d retries (give-ups since start: %d)",
                        page,
                        _MAX_RATE_LIMIT_RETRIES,
                        giveups,
                    )
                    raise RuntimeError(
                        f"Langfuse rate limit: gave up after {_MAX_RATE_LIMIT_RETRIES} retries (page {page})"
                    )
                try:
                    wait = int(resp.headers.get("Retry-After", "60"))
                except (ValueError, TypeError):
                    wait = 60
                log.warning(
                    "Langfuse rate limit hit (page %d), retry %d of %d in %ds",
                    page,
                    rate_limited,
                    _MAX_RATE_LIMIT_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            log.error("Langfuse observations fetch failed (page %d): %s", page, exc)
            raise
        rate_limited = 0

        rows.extend(body.get("data", []))
        next_cursor = (body.get("meta") or {}).get("cursor")
        if not next_cursor:
            return rows
        if next_cursor in used_cursors:
            raise RuntimeError(f"Langfuse observations returned a repeated cursor (page {page})")
        used_cursors.add(next_cursor)
        cursor = next_cursor
        page += 1


def fetch_observations_since(since: dt.datetime) -> Generator[Dict[str, Any], None, None]:
    """Yield GENERATION observations with the userId stored on each observation.

    Reads /api/public/v2/observations page by page, following meta.cursor until the
    response has none. The window is [since, now + 60 s); its upper bound is computed
    once so every page belongs to the same query. A page can be empty while a cursor is
    still present, so only a missing cursor ends the loop. A cursor that repeats raises.
    All pages are read before the first row is returned, so a failure on any page
    returns nothing. Before any row is returned, the fetched ids are compared with the ledger rows
    stored for the same window (see ledger_guard); a mismatch raises. Rows are mapped to the field
    names the ledger sync reads (see _to_v1_shape).
    """
    pk, sk, host = load_env()
    headers = auth_header(pk, sk)
    until = _now_utc() + _TO_START_TIME_MARGIN
    base_params = {
        "type": "GENERATION",
        "fromStartTime": _format_ts(since),
        "toStartTime": _format_ts(until),
        "fields": _FIELDS,
        "limit": _PAGE_SIZE,
    }

    raw_rows = _fetch_raw_rows(host, headers, base_params)
    _check_usage_group(raw_rows)
    ledger_guard.check_ledger_overlap([r["id"] for r in raw_rows if isinstance(r.get("id"), str)], since, until)
    _report_unusable_values(raw_rows)

    rows = []
    for row in raw_rows:
        obs = _to_v1_shape(row)
        obs["userId"] = _user_id_from_row(row)
        rows.append(obs)

    _fetch_stats.fetched = len(rows)
    yield from rows
