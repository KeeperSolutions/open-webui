import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator, Dict, Any, List

import requests
from cachetools import TTLCache

from open_webui.langfuse.metrics import auth_header, load_env

log = logging.getLogger(__name__)

_PAGE_SIZE = 500
_OBSERVATIONS_PATH = "/api/public/v2/observations"
# Field groups the poller reads: core (id, traceId, startTime), basic (name),
# model (model) and usage (usageDetails, totalCost).
_FIELDS = "core,basic,model,usage"
# toStartTime is exclusive, so it is set slightly ahead of now to tolerate clock skew.
_TO_START_TIME_MARGIN = dt.timedelta(seconds=60)
_USAGE_KEYS = ("input", "output", "total")
_TRACE_RESOLVE_WORKERS = 10
_trace_user_cache: TTLCache[str, str] = TTLCache(maxsize=10_000, ttl=7200)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _format_ts(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _to_v1_shape(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map one v2 observation row to the field names the ledger sync reads.

    A key that is absent in the v2 row stays absent in the result, so a missing
    usage or cost is not turned into zero. totalCost is converted without rounding.
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


def _fetch_one_trace(host: str, headers: dict, tid: str) -> tuple[str, str]:
    """Fetch a single trace and return (traceId, userId). Returns (tid, '') on failure."""
    try:
        resp = requests.get(
            f"{host}/api/public/traces/{tid}",
            headers=headers,
            timeout=15,
        )
        if resp.ok:
            return tid, resp.json().get("userId") or ""
    except Exception as exc:
        log.warning("Failed to fetch trace %s for userId: %s", tid, exc)
    return tid, ""


def _resolve_user_ids(
    host: str, headers: dict, trace_ids: List[str], pool: ThreadPoolExecutor
) -> Dict[str, str]:
    """Return {traceId: userId} for a batch of trace IDs.

    Cache hits are returned immediately; only uncached IDs are fetched from Langfuse.
    Resolved IDs are stored in _trace_user_cache (2h TTL, 10k cap) to reduce API calls
    across successive poller ticks.
    """
    if not trace_ids:
        return {}
    result: Dict[str, str] = {}
    to_fetch: List[str] = []
    for tid in trace_ids:
        if tid in _trace_user_cache:
            result[tid] = _trace_user_cache[tid]
        else:
            to_fetch.append(tid)

    if to_fetch:
        futures = {pool.submit(_fetch_one_trace, host, headers, tid): tid for tid in to_fetch}
        for future in as_completed(futures):
            tid, user_id = future.result()
            if user_id:
                _trace_user_cache[tid] = user_id
            result[tid] = user_id

    return result


def fetch_observations_since(since: dt.datetime) -> Generator[Dict[str, Any], None, None]:
    """Yield GENERATION observations enriched with userId from their parent trace.

    Reads /api/public/v2/observations page by page, following meta.cursor until the
    response has none. The window is [since, now + 60 s); its upper bound is computed
    once so every page belongs to the same query. A page can be empty while a cursor is
    still present, so only a missing cursor ends the loop. A cursor that repeats raises.
    Rows are mapped to the field names the ledger sync reads (see _to_v1_shape), then
    userId is resolved per page via /api/public/traces/{id}.
    A single shared ThreadPoolExecutor is reused across all pages to avoid
    per-page thread creation overhead.
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

    with ThreadPoolExecutor(max_workers=_TRACE_RESOLVE_WORKERS) as pool:
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

            if data:
                observations = [_to_v1_shape(row) for row in data]
                unique_trace_ids = list({obs["traceId"] for obs in observations if obs.get("traceId")})
                user_id_by_trace = _resolve_user_ids(host, headers, unique_trace_ids, pool)

                for obs in observations:
                    obs["userId"] = user_id_by_trace.get(obs.get("traceId", ""), "")
                    yield obs

            if not next_cursor:
                break
            if next_cursor in used_cursors:
                raise RuntimeError(f"Langfuse observations returned a repeated cursor (page {page})")
            used_cursors.add(next_cursor)
            cursor = next_cursor
            page += 1
