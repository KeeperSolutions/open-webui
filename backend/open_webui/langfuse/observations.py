import datetime as dt
import logging
import threading
import time
from typing import Generator, Dict, Any

import requests
from cachetools import TTLCache

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

# Observation ids already reported as having no userId, so rows re-read by the
# overlapping poll window or the nightly rescan are reported once, not on every read.
_missing_user_id_seen: TTLCache[str, bool] = TTLCache(maxsize=10_000, ttl=7200)
_missing_user_id_total = 0
_missing_user_id_lock = threading.Lock()


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _format_ts(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")


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
    key = obs_id if isinstance(obs_id, str) else None
    global _missing_user_id_total
    with _missing_user_id_lock:
        if key is not None and key in _missing_user_id_seen:
            return ""
        if key is not None:
            _missing_user_id_seen[key] = True
        _missing_user_id_total += 1
        total = _missing_user_id_total
    log.warning(
        "[ledger-poller] observation without userId (observation=%s, total without userId since start: %d)",
        obs_id,
        total,
    )
    return ""


def fetch_observations_since(since: dt.datetime) -> Generator[Dict[str, Any], None, None]:
    """Yield GENERATION observations with the userId stored on each observation.

    Reads /api/public/v2/observations page by page, following meta.cursor until the
    response has none. The window is [since, now + 60 s); its upper bound is computed
    once so every page belongs to the same query. A page can be empty while a cursor is
    still present, so only a missing cursor ends the loop. A cursor that repeats raises.
    Rows are mapped to the field names the ledger sync reads (see _to_v1_shape).
    """
    pk, sk, host = load_env()
    headers = auth_header(pk, sk)
    base_params = {
        "type": "GENERATION",
        "fromStartTime": _format_ts(since),
        "toStartTime": _format_ts(_now_utc() + _TO_START_TIME_MARGIN),
        "fields": _FIELDS,
        "limit": _PAGE_SIZE,
    }

    page = 1
    cursor = None
    used_cursors: set = set()
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
                try:
                    wait = int(resp.headers.get("Retry-After", "60"))
                except (ValueError, TypeError):
                    wait = 60
                log.warning("Langfuse rate limit hit (page %d), retrying in %ds", page, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            log.error("Langfuse observations fetch failed (page %d): %s", page, exc)
            raise

        data = body.get("data", [])
        next_cursor = (body.get("meta") or {}).get("cursor")

        for row in data:
            obs = _to_v1_shape(row)
            obs["userId"] = _user_id_from_row(row)
            yield obs

        if not next_cursor:
            break
        if next_cursor in used_cursors:
            raise RuntimeError(f"Langfuse observations returned a repeated cursor (page {page})")
        used_cursors.add(next_cursor)
        cursor = next_cursor
        page += 1
