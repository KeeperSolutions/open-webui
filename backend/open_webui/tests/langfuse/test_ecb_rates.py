"""Tests for langfuse/ecb_rates.py — ECB rate fetcher with two-tier cache."""
import time
from unittest.mock import MagicMock, patch

import pytest

import open_webui.langfuse.ecb_rates as ecb


def _make_xml(value: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<message:GenericData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"'
        ' xmlns:generic="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic">'
        "<message:DataSet><generic:Series><generic:Obs>"
        f'<generic:ObsValue value="{value}"/>'
        "</generic:Obs></generic:Series></message:DataSet>"
        "</message:GenericData>"
    )


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset module-level cache state before each test."""
    ecb._cached_rate = None
    ecb._cache_expires_at = 0.0
    ecb._last_known_rate = None
    yield
    ecb._cached_rate = None
    ecb._cache_expires_at = 0.0
    ecb._last_known_rate = None


def _mock_success(value: float):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = _make_xml(str(value))
    return mock_resp


def _mock_failure():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("connection refused")
    return mock_resp


class TestGetEurUsdRate:
    def test_returns_float_on_success(self):
        with patch("open_webui.langfuse.ecb_rates.requests.get", return_value=_mock_success(1.1467)):
            rate = ecb.get_eur_usd_rate()
        assert rate == pytest.approx(1.1467)

    def test_caches_successful_rate_for_four_hours(self):
        with patch("open_webui.langfuse.ecb_rates.requests.get", return_value=_mock_success(1.1467)) as mock_get:
            ecb.get_eur_usd_rate()
            ecb.get_eur_usd_rate()
        assert mock_get.call_count == 1  # second call served from cache

    def test_updates_last_known_rate_on_success(self):
        with patch("open_webui.langfuse.ecb_rates.requests.get", return_value=_mock_success(1.1467)):
            ecb.get_eur_usd_rate()
        assert ecb._last_known_rate == pytest.approx(1.1467)

    def test_returns_last_known_rate_when_ecb_down(self):
        # First call succeeds → establishes last_known_rate
        with patch("open_webui.langfuse.ecb_rates.requests.get", return_value=_mock_success(1.1467)):
            ecb.get_eur_usd_rate()

        # Expire cache so next call hits ECB again
        ecb._cache_expires_at = 0.0

        # Second call fails
        with patch("open_webui.langfuse.ecb_rates.requests.get", side_effect=Exception("timeout")):
            rate = ecb.get_eur_usd_rate()

        assert rate == pytest.approx(1.1467)

    def test_returns_fallback_rate_when_ecb_down_and_no_prior_rate(self):
        """ECB never returns None anymore — falls back to _FALLBACK_RATE so
        cost_eur is never NULL (which would show as 0 credits used in billing)."""
        with patch("open_webui.langfuse.ecb_rates.requests.get", side_effect=Exception("timeout")):
            rate = ecb.get_eur_usd_rate()
        assert rate == ecb._FALLBACK_RATE

    def test_failure_cached_for_fifteen_minutes_not_four_hours(self):
        with patch("open_webui.langfuse.ecb_rates.requests.get", side_effect=Exception("timeout")) as mock_get:
            ecb.get_eur_usd_rate()
            # Advance by 14 minutes — still within failure TTL, should not retry
            ecb._cache_expires_at = time.monotonic() + 60  # 1 min remaining
            ecb.get_eur_usd_rate()
        assert mock_get.call_count == 1  # served from failure cache

    def test_failure_ttl_is_shorter_than_success_ttl(self):
        assert ecb._TTL_FAILURE < ecb._TTL_SUCCESS

    def test_cost_conversion_formula(self):
        """cost_eur = cost_usd / rate. Spot-check with known values."""
        rate = 1.1467
        cost_usd = 0.12574
        expected_eur = cost_usd / rate
        assert expected_eur == pytest.approx(0.10965, rel=1e-3)

    def test_successful_fetch_resets_failure_cache(self):
        """After a failure, a later successful call should cache for 4 hours (not 15 min)."""
        with patch("open_webui.langfuse.ecb_rates.requests.get", side_effect=Exception("timeout")):
            ecb.get_eur_usd_rate()

        ecb._cache_expires_at = 0.0  # expire failure cache

        before = time.monotonic()
        with patch("open_webui.langfuse.ecb_rates.requests.get", return_value=_mock_success(1.15)):
            ecb.get_eur_usd_rate()

        # Success TTL (~4h) should be much larger than failure TTL (~15min)
        remaining = ecb._cache_expires_at - before
        assert remaining > ecb._TTL_FAILURE  # at minimum more than 15 min
