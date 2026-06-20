import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator, Dict, Any, List

import requests

from open_webui.langfuse.metrics import auth_header, load_env

log = logging.getLogger(__name__)

_PAGE_SIZE = 100
_TRACE_RESOLVE_WORKERS = 20


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
    """Return {traceId: userId} for a batch of trace IDs, fetched concurrently via shared pool."""
    if not trace_ids:
        return {}
    result: Dict[str, str] = {}
    futures = {pool.submit(_fetch_one_trace, host, headers, tid): tid for tid in trace_ids}
    for future in as_completed(futures):
        tid, user_id = future.result()
        result[tid] = user_id
    return result


def fetch_observations_since(since: dt.datetime) -> Generator[Dict[str, Any], None, None]:
    """Yield GENERATION observations enriched with userId from their parent trace.

    Uses /api/public/observations (has model, tokens, calculatedTotalCost) then
    batch-resolves userId per page via /api/public/traces/{id}.
    A single shared ThreadPoolExecutor is reused across all pages to avoid
    per-page thread creation overhead.
    """
    pk, sk, host = load_env()
    headers = auth_header(pk, sk)
    from_ts = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    with ThreadPoolExecutor(max_workers=_TRACE_RESOLVE_WORKERS) as pool:
        page = 1
        while True:
            try:
                resp = requests.get(
                    f"{host}/api/public/observations",
                    headers=headers,
                    params={
                        "type": "GENERATION",
                        "fromStartTime": from_ts,
                        "page": page,
                        "limit": _PAGE_SIZE,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                body = resp.json()
            except Exception as exc:
                log.error("Langfuse observations fetch failed (page %d): %s", page, exc)
                raise

            data = body.get("data", [])
            meta = body.get("meta", {})

            if data:
                unique_trace_ids = list({obs["traceId"] for obs in data if obs.get("traceId")})
                user_id_by_trace = _resolve_user_ids(host, headers, unique_trace_ids, pool)

                for obs in data:
                    obs["userId"] = user_id_by_trace.get(obs.get("traceId", ""), "")
                    yield obs

            total_pages = meta.get("totalPages", 1)
            if page >= total_pages:
                break
            page += 1
