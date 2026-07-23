import logging
import os
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

# Fallback rate when ECB is unreachable and no historical rate exists
# Based on historical EUR/USD average (1 EUR ≈ 1.10 USD)
# This prevents NULL cost_eur rows that would show as 0 credits used in billing
# Can be overridden via ECB_FALLBACK_RATE environment variable
_FALLBACK_RATE = float(os.environ.get("ECB_FALLBACK_RATE", "1.10"))

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
        log.warning("ECB rate fetch failed: %s", sanitized)
        return None


def get_eur_usd_rate() -> float:
    """Return USD-per-EUR exchange rate from ECB (e.g. 1.1467 means 1 EUR = 1.1467 USD).

    Use as: cost_eur = cost_usd / rate

    Returns:
        - Live ECB rate if available
        - Last known good rate if ECB temporarily unreachable
        - Fallback rate (1.10) if ECB has never responded since process start
    
    Never returns None to prevent NULL cost_eur rows that show as 0 credits in billing.
    """
    global _cached_rate, _cache_expires_at, _last_known_rate

    with _lock:
        now = time.monotonic()
        if now < _cache_expires_at:
            # Cache still valid — return last known rate if cached value is None
            return _cached_rate if _cached_rate is not None else _last_known_rate or _FALLBACK_RATE

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
            log.warning(
                "ECB unreachable and no last known rate available — using fallback rate %.2f",
                _FALLBACK_RATE
            )
            return _FALLBACK_RATE
