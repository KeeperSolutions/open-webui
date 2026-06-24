"""Tests for langfuse/observations.py — paginating Langfuse observations client."""
import datetime
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

import open_webui.langfuse.observations as obs_module
from open_webui.langfuse.observations import fetch_observations_since, _resolve_user_ids


def _make_response(data: list, page: int, total_pages: int) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": data,
        "meta": {"page": page, "limit": 100, "totalPages": total_pages, "totalItems": len(data)},
    }
    return mock_resp


def _make_trace_response(user_id: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"userId": user_id}
    return mock_resp


OBS_1 = {
    "id": "obs_001",
    "type": "GENERATION",
    "traceId": "trace_aaa",
    "startTime": "2026-06-19T10:00:00.000Z",
    "model": "gpt-4o",
    "usage": {"input": 500, "output": 100, "total": 600},
    "calculatedTotalCost": 0.0045,
}

OBS_2 = {
    "id": "obs_002",
    "type": "GENERATION",
    "traceId": "trace_bbb",
    "startTime": "2026-06-19T11:00:00.000Z",
    "model": "gpt-4o",
    "usage": {"input": 200, "output": 50, "total": 250},
    "calculatedTotalCost": 0.0012,
}

SINCE = datetime.datetime(2026, 6, 19, 0, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def patch_load_env():
    with patch("open_webui.langfuse.observations.load_env", return_value=("pk", "sk", "https://cloud.langfuse.com")):
        yield


@pytest.fixture(autouse=True)
def clear_trace_cache():
    obs_module._trace_user_cache.clear()
    yield
    obs_module._trace_user_cache.clear()


class TestResolveUserIds:
    def test_returns_empty_dict_for_no_trace_ids(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = _resolve_user_ids("https://host", {}, [], pool)
        assert result == {}

    def test_resolves_single_trace(self):
        mock_resp = _make_trace_response("user@example.com")
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = _resolve_user_ids("https://host", {}, ["trace_aaa"], pool)
        assert result == {"trace_aaa": "user@example.com"}

    def test_resolves_multiple_traces_concurrently(self):
        def side_effect(url, **kwargs):
            if "trace_aaa" in url:
                return _make_trace_response("alice@example.com")
            if "trace_bbb" in url:
                return _make_trace_response("bob@example.com")
            raise ValueError(f"unexpected url: {url}")

        with patch("open_webui.langfuse.observations.requests.get", side_effect=side_effect):
            with ThreadPoolExecutor(max_workers=2) as pool:
                result = _resolve_user_ids("https://host", {}, ["trace_aaa", "trace_bbb"], pool)
        assert result["trace_aaa"] == "alice@example.com"
        assert result["trace_bbb"] == "bob@example.com"

    def test_returns_empty_string_on_http_failure(self):
        mock_resp = MagicMock()
        mock_resp.ok = False
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = _resolve_user_ids("https://host", {}, ["trace_aaa"], pool)
        assert result == {"trace_aaa": ""}

    def test_returns_empty_string_on_exception(self):
        with patch("open_webui.langfuse.observations.requests.get", side_effect=Exception("timeout")):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = _resolve_user_ids("https://host", {}, ["trace_aaa"], pool)
        assert result == {"trace_aaa": ""}

    def test_handles_none_user_id_in_trace(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"userId": None}
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = _resolve_user_ids("https://host", {}, ["trace_aaa"], pool)
        assert result["trace_aaa"] == ""

    def test_cache_miss_fetches_and_stores(self):
        mock_resp = _make_trace_response("user@example.com")
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp) as mock_get:
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = _resolve_user_ids("https://host", {}, ["trace_aaa"], pool)
        assert result == {"trace_aaa": "user@example.com"}
        assert mock_get.call_count == 1
        assert obs_module._trace_user_cache["trace_aaa"] == "user@example.com"

    def test_cache_hit_skips_fetch(self):
        obs_module._trace_user_cache["trace_aaa"] = "cached@example.com"
        with patch("open_webui.langfuse.observations.requests.get") as mock_get:
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = _resolve_user_ids("https://host", {}, ["trace_aaa"], pool)
        assert result == {"trace_aaa": "cached@example.com"}
        mock_get.assert_not_called()

    def test_cache_partial_hit_only_fetches_misses(self):
        obs_module._trace_user_cache["trace_aaa"] = "alice@example.com"

        def side_effect(url, **kwargs):
            if "trace_bbb" in url:
                return _make_trace_response("bob@example.com")
            raise ValueError(f"unexpected url: {url}")

        with patch("open_webui.langfuse.observations.requests.get", side_effect=side_effect) as mock_get:
            with ThreadPoolExecutor(max_workers=2) as pool:
                result = _resolve_user_ids("https://host", {}, ["trace_aaa", "trace_bbb"], pool)

        assert result["trace_aaa"] == "alice@example.com"
        assert result["trace_bbb"] == "bob@example.com"
        assert mock_get.call_count == 1  # only trace_bbb fetched


class TestFetchObservationsSince:
    def test_yields_observations_from_single_page(self):
        mock_resp = _make_response([OBS_1, OBS_2], page=1, total_pages=1)
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp), \
             patch("open_webui.langfuse.observations._resolve_user_ids",
                   return_value={"trace_aaa": "user@example.com", "trace_bbb": "other@example.com"}):
            results = list(fetch_observations_since(SINCE))
        assert len(results) == 2
        assert results[0]["id"] == "obs_001"
        assert results[1]["id"] == "obs_002"

    def test_enriches_observations_with_user_id(self):
        mock_resp = _make_response([OBS_1], page=1, total_pages=1)
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp), \
             patch("open_webui.langfuse.observations._resolve_user_ids",
                   return_value={"trace_aaa": "user@example.com"}):
            results = list(fetch_observations_since(SINCE))
        assert results[0]["userId"] == "user@example.com"

    def test_user_id_falls_back_to_empty_string_when_trace_missing(self):
        mock_resp = _make_response([OBS_1], page=1, total_pages=1)
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp), \
             patch("open_webui.langfuse.observations._resolve_user_ids", return_value={}):
            results = list(fetch_observations_since(SINCE))
        assert results[0]["userId"] == ""

    def test_deduplicates_trace_ids_before_resolve(self):
        obs_same_trace = {**OBS_1, "id": "obs_003", "traceId": "trace_aaa"}
        page = _make_response([OBS_1, obs_same_trace], page=1, total_pages=1)
        with patch("open_webui.langfuse.observations.requests.get", return_value=page), \
             patch("open_webui.langfuse.observations._resolve_user_ids",
                   return_value={"trace_aaa": "user@example.com"}) as mock_resolve:
            list(fetch_observations_since(SINCE))
        # Both obs share trace_aaa — should only be resolved once
        called_trace_ids = mock_resolve.call_args[0][2]
        assert called_trace_ids == ["trace_aaa"]

    def test_paginates_across_multiple_pages(self):
        page1 = _make_response([OBS_1], page=1, total_pages=2)
        page2 = _make_response([OBS_2], page=2, total_pages=2)
        with patch("open_webui.langfuse.observations.requests.get", side_effect=[page1, page2]), \
             patch("open_webui.langfuse.observations._resolve_user_ids", return_value={}):
            results = list(fetch_observations_since(SINCE))
        assert len(results) == 2

    def test_stops_at_total_pages(self):
        page1 = _make_response([OBS_1], page=1, total_pages=1)
        with patch("open_webui.langfuse.observations.requests.get", return_value=page1) as mock_get, \
             patch("open_webui.langfuse.observations._resolve_user_ids", return_value={}):
            list(fetch_observations_since(SINCE))
        assert mock_get.call_count == 1

    def test_handles_empty_result_set(self):
        mock_resp = _make_response([], page=1, total_pages=1)
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp):
            results = list(fetch_observations_since(SINCE))
        assert results == []

    def test_raises_on_http_error_so_watermark_does_not_advance(self):
        with patch("open_webui.langfuse.observations.requests.get", side_effect=Exception("timeout")):
            with pytest.raises(Exception, match="timeout"):
                list(fetch_observations_since(SINCE))

    def test_passes_from_start_time_param(self):
        mock_resp = _make_response([], page=1, total_pages=1)
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp) as mock_get:
            list(fetch_observations_since(SINCE))
        call_kwargs = mock_get.call_args
        params = call_kwargs[1]["params"] if call_kwargs[1] else call_kwargs[0][1]
        assert "fromStartTime" in params
        assert "2026-06-19" in params["fromStartTime"]

    def test_passes_type_generation_filter(self):
        mock_resp = _make_response([], page=1, total_pages=1)
        with patch("open_webui.langfuse.observations.requests.get", return_value=mock_resp) as mock_get:
            list(fetch_observations_since(SINCE))
        call_kwargs = mock_get.call_args
        params = call_kwargs[1]["params"] if call_kwargs[1] else call_kwargs[0][1]
        assert params.get("type") == "GENERATION"
