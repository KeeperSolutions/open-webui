import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

log = logging.getLogger(__name__)

_ECB_URL = (
    "https://data-api.ecb.europa.eu/service/data/EXR/"
    "D.USD.EUR.SP00.A?format=xmldata&lastNObservations=1"
)
_NS = {"generic": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic"}

_TTL_SUCCESS = 4 * 3600   # 4 hours when ECB responds
_TTL_FAILURE = 15 * 60    # 15 minutes before retrying after a failed fetch

_lock = threading.Lock()
_cached_rate: Optional[float] = None   # current cached value (None = failure cached)
_cache_expires_at: float = 0.0         # epoch when cache expires
_last_known_rate: Optional[float] = None  # last successful rate, never reset
_last_error: Optional[str] = None        # last fetch error message


def _fetch_rate() -> Optional[float]:
    global _last_error
    try:
        resp = requests.get(_ECB_URL, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        obs = root.find(".//generic:ObsValue", _NS)
        if obs is None:
            raise ValueError("generic:ObsValue not found in ECB response")
        _last_error = None
        return float(obs.get("value"))
    except Exception as exc:
        # Strip embedded credentials from proxy URLs (e.g. http://user:pass@host)
        sanitized = re.sub(r"://[^@]+@", "://<redacted>@", str(exc))
        _last_error = sanitized[:500]  # cap length to avoid leaking large traces
        log.warning("ECB rate fetch failed: %s", exc)
        return None


def get_eur_usd_rate() -> Optional[float]:
    """Return USD-per-EUR exchange rate from ECB (e.g. 1.1467 means 1 EUR = 1.1467 USD).

    Use as: cost_eur = cost_usd / rate

    Returns None only when ECB has never responded since process start.
    Falls back to last known good rate when ECB is temporarily unreachable.
    """
    global _cached_rate, _cache_expires_at, _last_known_rate

    with _lock:
        now = time.monotonic()
        if now < _cache_expires_at:
            # Cache still valid — return last known rate if cached value is None
            return _cached_rate if _cached_rate is not None else _last_known_rate

        rate = _fetch_rate()
        if rate is not None:
            _cached_rate = rate
            _last_known_rate = rate
            _cache_expires_at = now + _TTL_SUCCESS
            return rate
        else:
            _cached_rate = None
            _cache_expires_at = now + _TTL_FAILURE
            if _last_known_rate is not None:
                log.warning(
                    "ECB unreachable — using last known rate %.6f", _last_known_rate
                )
                return _last_known_rate
            log.error(
                "ECB unreachable and no last known rate available — cost_eur will be NULL"
            )
            return None
