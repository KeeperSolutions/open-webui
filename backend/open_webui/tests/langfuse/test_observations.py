"""Tests for langfuse/observations.py: the v2 observations client and its row mapping.

The HTTP layer is replaced by FakeLangfuse, which serves synthetic v2 pages built with
make_v2_row/make_v2_page. The tests marked real snapshot read recorded responses from the
directory named by LANGFUSE_SNAPSHOT_DIR and are skipped when it is not set.
"""
import copy
import datetime
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
import requests

import open_webui.langfuse.observations as obs_module
from open_webui.langfuse.observations import _resolve_user_ids, _to_v1_shape, fetch_observations_since
from open_webui.tests.langfuse.test_v2_observations_shape import (  # noqa: F401  (snapshot_dir is a fixture)
    _load_snapshot,
    make_v2_page,
    make_v2_row,
    snapshot_dir,
)

SINCE = datetime.datetime(2026, 6, 19, 0, 0, 0, tzinfo=datetime.timezone.utc)
NOW = datetime.datetime(2026, 6, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
GET = "open_webui.langfuse.observations.requests.get"
V2_URL = "https://host/api/public/v2/observations"


def _make_trace_response(user_id: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"userId": user_id}
    return mock_resp


def _response(body: dict, status: int = 200, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.ok = status < 400
    resp.json.return_value = body
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    return resp


class FakeLangfuse:
    """Serves queued v2 pages and per-trace user lookups, and records every request."""

    def __init__(self, pages: list, users: dict | None = None):
        self.pages = list(pages)
        self.users = users or {}
        self.calls: list = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == V2_URL:
            item = self.pages.pop(0)
            if isinstance(item, Exception):
                raise item
            return item if isinstance(item, MagicMock) else _response(item)
        if "/api/public/traces/" in url:
            return _make_trace_response(self.users.get(url.rsplit("/", 1)[1], ""))
        raise AssertionError(f"unexpected url: {url}")

    @property
    def observation_calls(self) -> list:
        return [(u, k) for u, k in self.calls if u == V2_URL]

    def params(self, i: int) -> dict:
        return self.observation_calls[i][1]["params"]

    @property
    def trace_urls(self) -> list:
        return [u for u, _ in self.calls if "/api/public/traces/" in u]


def _fetch(fake: FakeLangfuse, since: datetime.datetime = SINCE) -> list:
    with patch(GET, side_effect=fake.get):
        return list(fetch_observations_since(since))


@pytest.fixture(autouse=True)
def patch_environment():
    with patch("open_webui.langfuse.observations.load_env", return_value=("pk", "sk", "https://host")), \
         patch("open_webui.langfuse.observations._now_utc", return_value=NOW):
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


class TestRequest:
    def test_targets_v2_endpoint_with_bounded_window(self):
        """One request to the v2 endpoint with exactly the agreed parameters and basic auth."""
        fake = FakeLangfuse([make_v2_page([])])
        _fetch(fake)
        url, kwargs = fake.observation_calls[0]
        assert url == V2_URL
        assert kwargs["params"] == {
            "type": "GENERATION",
            "fromStartTime": "2026-06-19T00:00:00.000Z",
            "toStartTime": "2026-06-19T12:01:00.000Z",
            "fields": "core,basic,model,usage",
            "limit": 500,
        }
        assert kwargs["headers"]["Authorization"].startswith("Basic ")
        assert kwargs["timeout"] == 30

    def test_from_start_time_follows_since(self):
        """fromStartTime is the since argument formatted in UTC, not a fixed value."""
        fake = FakeLangfuse([make_v2_page([])])
        _fetch(fake, since=datetime.datetime(2026, 6, 18, 23, 58, 7, tzinfo=datetime.timezone.utc))
        assert fake.params(0)["fromStartTime"] == "2026-06-18T23:58:07.000Z"

    def test_to_start_time_is_computed_once_for_all_pages(self):
        """Every page of one fetch uses the same upper bound, so the cursor stays valid."""
        fake = FakeLangfuse([
            make_v2_page([make_v2_row(1)], cursor="c1"),
            make_v2_page([make_v2_row(2)], cursor="c2"),
            make_v2_page([make_v2_row(3)]),
        ])
        later = [NOW + datetime.timedelta(minutes=m) for m in (0, 5, 10, 15)]
        with patch("open_webui.langfuse.observations._now_utc", side_effect=later) as clock:
            _fetch(fake)
        assert {fake.params(i)["toStartTime"] for i in range(3)} == {"2026-06-19T12:01:00.000Z"}
        assert clock.call_count == 1

    def test_no_v1_pagination_or_io_parameters(self):
        """The v2 request has no page parameter, no parseIoAsJson and no io/metadata field groups."""
        fake = FakeLangfuse([make_v2_page([])])
        _fetch(fake)
        params = fake.params(0)
        assert "page" not in params and "parseIoAsJson" not in params
        assert not {"io", "metadata"} & set(params["fields"].split(","))


class TestCursorLoop:
    def test_follows_cursor_across_pages(self):
        """Each request carries the cursor of the previous response; other parameters do not change."""
        fake = FakeLangfuse([
            make_v2_page([make_v2_row(1), make_v2_row(2)], cursor="c1"),
            make_v2_page([make_v2_row(3)], cursor="c2"),
            make_v2_page([make_v2_row(4)]),
        ])
        results = _fetch(fake)
        assert [r["id"] for r in results] == [f"{n:016x}" for n in (1, 2, 3, 4)]
        assert len(fake.observation_calls) == 3
        assert "cursor" not in fake.params(0)
        assert fake.params(1)["cursor"] == "c1"
        assert fake.params(2)["cursor"] == "c2"
        unchanged = {k: v for k, v in fake.params(0).items()}
        for i in (1, 2):
            assert {k: v for k, v in fake.params(i).items() if k != "cursor"} == unchanged

    def test_cursor_is_sent_unchanged(self):
        """The cursor is an opaque string and is passed to requests exactly as received."""
        token = "eyJhIjoiYitjL2Q9In0_-%2F+/=="
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor=token), make_v2_page([])])
        _fetch(fake)
        assert fake.params(1)["cursor"] == token

    @pytest.mark.parametrize(
        "body",
        [
            {"data": [], "meta": {}},
            {"data": [], "meta": {"cursor": None}},
            {"data": [], "meta": {"cursor": ""}},
            {"data": []},
            {"data": [], "meta": None},
        ],
        ids=["meta_empty", "cursor_null", "cursor_empty_string", "meta_absent", "meta_null"],
    )
    def test_stops_when_there_is_no_cursor(self, body):
        """A response without a usable cursor is the last page."""
        fake = FakeLangfuse([body])
        assert _fetch(fake) == []
        assert len(fake.observation_calls) == 1

    def test_empty_page_with_cursor_continues(self):
        """A page with no rows but a cursor does not end the loop."""
        fake = FakeLangfuse([make_v2_page([], cursor="c1"), make_v2_page([make_v2_row(1)])])
        results = _fetch(fake)
        assert len(results) == 1
        assert len(fake.observation_calls) == 2
        assert fake.params(1)["cursor"] == "c1"

    def test_short_page_with_cursor_continues(self):
        """Fewer rows than the limit does not end the loop while a cursor is present."""
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor="c1"), make_v2_page([make_v2_row(2)])])
        assert len(_fetch(fake)) == 2

    def test_v1_pagination_metadata_does_not_drive_the_loop(self):
        """totalPages in meta is ignored: no cursor ends the loop even when more pages are advertised."""
        body = make_v2_page([make_v2_row(1)])
        body["meta"] = {"page": 1, "totalPages": 3}
        fake = FakeLangfuse([body, make_v2_page([make_v2_row(2)])])
        assert len(_fetch(fake)) == 1
        assert len(fake.observation_calls) == 1

    def test_repeated_cursor_raises(self):
        """A cursor equal to the one just used would loop forever, so it raises."""
        fake = FakeLangfuse([
            make_v2_page([make_v2_row(1)], cursor="c1"),
            make_v2_page([make_v2_row(2)], cursor="c1"),
            make_v2_page([make_v2_row(3)]),
        ])
        with pytest.raises(RuntimeError, match=r"^Langfuse observations returned a repeated cursor \(page 2\)$"):
            _fetch(fake)
        assert len(fake.observation_calls) == 2

    def test_cursor_seen_earlier_in_the_loop_raises(self):
        """A cursor that returns to an earlier value (A, B, A) also raises."""
        fake = FakeLangfuse([
            make_v2_page([], cursor="c1"),
            make_v2_page([], cursor="c2"),
            make_v2_page([], cursor="c1"),
            make_v2_page([]),
        ])
        with pytest.raises(RuntimeError, match=r"\(page 3\)$"):
            _fetch(fake)
        assert len(fake.observation_calls) == 3

    def test_distinct_cursors_are_not_mistaken_for_repeats(self):
        """Many different cursors never raise."""
        pages = [make_v2_page([], cursor=f"c{i}") for i in range(30)] + [make_v2_page([make_v2_row(1)])]
        assert len(_fetch(FakeLangfuse(pages))) == 1


class TestFailures:
    @pytest.mark.parametrize("status", [400, 401, 404, 500, 503])
    def test_http_error_status_raises(self, status):
        """A 4xx or 5xx response raises and is not retried."""
        fake = FakeLangfuse([_response({}, status=status)])
        with pytest.raises(requests.HTTPError):
            _fetch(fake)
        assert len(fake.observation_calls) == 1

    def test_error_on_a_later_page_raises(self):
        """A failure after the first page raises out of the generator."""
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor="c1"), _response({}, status=500)])
        with pytest.raises(requests.HTTPError):
            _fetch(fake)

    def test_raises_on_transport_error(self):
        """A timeout propagates so the caller does not advance the watermark."""
        fake = FakeLangfuse([requests.Timeout("timeout")])
        with pytest.raises(requests.Timeout):
            _fetch(fake)

    def test_429_waits_retry_after_and_repeats_the_same_request(self):
        """A 429 sleeps for Retry-After and resends the same cursor."""
        fake = FakeLangfuse([
            make_v2_page([make_v2_row(1)], cursor="c1"),
            _response({}, status=429, headers={"Retry-After": "7"}),
            make_v2_page([make_v2_row(2)]),
        ])
        with patch("open_webui.langfuse.observations.time.sleep") as sleep:
            results = _fetch(fake)
        sleep.assert_called_once_with(7)
        assert len(results) == 2
        assert fake.params(1) == fake.params(2)
        assert fake.params(2)["cursor"] == "c1"

    def test_429_without_a_numeric_retry_after_waits_60_seconds(self):
        fake = FakeLangfuse([_response({}, status=429, headers={"Retry-After": "soon"}), make_v2_page([])])
        with patch("open_webui.langfuse.observations.time.sleep") as sleep:
            _fetch(fake)
        sleep.assert_called_once_with(60)


class TestRowMapping:
    def test_maps_each_field_the_ledger_sync_reads(self):
        """Explicit expected values for id, traceId, name, model, startTime, usage and cost."""
        rows = [
            make_v2_row(1, totalCost=0.00448125),
            make_v2_row(2, totalCost=0.020292499999),
        ]
        assert [_to_v1_shape(r) for r in rows] == [
            {
                "id": "0000000000000001",
                "traceId": "00000000000000000000000000000001",
                "name": "llm_response",
                "model": "model-a",
                "startTime": "2026-01-01T10:00:01.250Z",
                "usage": {"input": 100, "output": 20, "total": 120},
                "calculatedTotalCost": 0.00448125,
            },
            {
                "id": "0000000000000002",
                "traceId": "00000000000000000000000000000002",
                "name": "llm_response",
                "model": "model-a",
                "startTime": "2026-01-01T10:00:02.250Z",
                "usage": {"input": 200, "output": 40, "total": 240},
                "calculatedTotalCost": 0.020292499999,
            },
        ]

    def test_output_has_only_the_mapped_keys(self):
        """v2-only fields (userId, sessionId, inputUsage, totalCost, usageDetails, ...) are not passed on."""
        out = _to_v1_shape(make_v2_row(1))
        assert set(out) == {"id", "traceId", "name", "model", "startTime", "usage", "calculatedTotalCost"}

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("0.020292499999", 0.020292499999),
            ("5e-06", 5e-06),
            ("0.1234567890123456789", 0.12345678901234568),
            (3, 3.0),
            (0, 0.0),
        ],
    )
    def test_cost_is_converted_to_float_without_rounding(self, value, expected):
        out = _to_v1_shape(make_v2_row(1, totalCost=value))
        assert out["calculatedTotalCost"] == expected
        assert isinstance(out["calculatedTotalCost"], float)

    def test_null_cost_stays_null(self):
        assert _to_v1_shape(make_v2_row(1, totalCost=None))["calculatedTotalCost"] is None

    def test_absent_cost_stays_absent(self):
        row = make_v2_row(1)
        del row["totalCost"]
        assert "calculatedTotalCost" not in _to_v1_shape(row)

    def test_unparseable_cost_is_passed_through(self):
        """An unparseable cost is not changed here; the ledger sync treats it as missing."""
        assert _to_v1_shape(make_v2_row(1, totalCost="n/a"))["calculatedTotalCost"] == "n/a"

    def test_absent_usage_details_leaves_usage_absent(self):
        row = make_v2_row(1)
        del row["usageDetails"]
        assert "usage" not in _to_v1_shape(row)

    @pytest.mark.parametrize("value", [None, [1, 2], "x", 7])
    def test_usage_details_that_is_not_an_object_leaves_usage_absent(self, value):
        assert "usage" not in _to_v1_shape(make_v2_row(1, usageDetails=value))

    def test_empty_usage_details_gives_empty_usage(self):
        assert _to_v1_shape(make_v2_row(1, usageDetails={}))["usage"] == {}

    def test_usage_keeps_only_the_keys_the_ledger_reads_and_never_invents_one(self):
        out = _to_v1_shape(make_v2_row(1, usageDetails={"input": 5, "input_cached": 3, "output": 2}))
        assert out["usage"] == {"input": 5, "output": 2}

    def test_row_without_id_has_no_id(self):
        row = make_v2_row(1)
        del row["id"]
        assert "id" not in _to_v1_shape(row)

    def test_does_not_modify_the_input_row(self):
        row = make_v2_row(1)
        before = copy.deepcopy(row)
        _to_v1_shape(row)
        assert row == before


class TestUserIdFromTrace:
    def test_user_id_comes_from_the_trace_lookup(self):
        """userId is still resolved through /api/public/traces/{id}; the row's own userId is not used yet."""
        row = make_v2_row(1)
        fake = FakeLangfuse([make_v2_page([row])], users={row["traceId"]: "trace-user@example.test"})
        results = _fetch(fake)
        assert results[0]["userId"] == "trace-user@example.test"
        assert row["userId"] != "trace-user@example.test"

    def test_user_id_is_empty_when_the_trace_has_none(self):
        assert _fetch(FakeLangfuse([make_v2_page([make_v2_row(1)])]))[0]["userId"] == ""

    def test_a_trace_shared_by_several_rows_is_looked_up_once(self):
        rows = [make_v2_row(1), make_v2_row(2, traceId=make_v2_row(1)["traceId"])]
        fake = FakeLangfuse([make_v2_page(rows)])
        _fetch(fake)
        assert len(fake.trace_urls) == 1

    def test_no_trace_lookup_for_an_empty_page(self):
        fake = FakeLangfuse([make_v2_page([], cursor="c1"), make_v2_page([])])
        _fetch(fake)
        assert fake.trace_urls == []


def _run_sync(fake: FakeLangfuse):
    """Run the real _sync_observations over fake Langfuse pages; return the ledger mock."""
    import open_webui.tasks.billing as tasks_mod

    ledger = MagicMock()
    ledger.bulk_insert_ignore.side_effect = lambda rows: {r["langfuse_observation_id"] for r in rows}
    ledger.bulk_upsert_costs.return_value = set()
    ledger.get_cost_eur_for_users_current_month.return_value = {}
    ledger.get_models_with_recent_priced_rows.return_value = []
    alerts = MagicMock()
    alerts.try_claim_alert.return_value = False
    alerts.get_alerted_keys.return_value = set()

    with patch("open_webui.models.usage_ledger.UsageLedgerDB", ledger), \
         patch("open_webui.models.billing_alert_state.BillingAlertStateDB", alerts), \
         patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=2.0), \
         patch("open_webui.models.users.Users.get_super_admin_user", return_value=None), \
         patch(GET, side_effect=fake.get):
        tasks_mod._sync_observations(SINCE)
    return ledger


class TestLedgerSyncReadsMappedRows:
    def test_values_reach_the_ledger_rows(self):
        """Tokens, cost, model, time and user in the inserted rows equal the values in the v2 rows."""
        rows = [make_v2_row(1, totalCost=0.5), make_v2_row(2, totalCost=0.25)]
        fake = FakeLangfuse([make_v2_page(rows)], users={rows[0]["traceId"]: "u1@example.test"})
        inserted = _run_sync(fake).bulk_insert_ignore.call_args.args[0]
        by_id = {r["langfuse_observation_id"]: r for r in inserted}
        first = by_id["0000000000000001"]
        assert (first["tokens_input"], first["tokens_output"], first["tokens_total"]) == (100, 20, 120)
        assert first["cost_usd"] == 0.5 and first["cost_eur"] == 0.25 and first["eur_usd_rate"] == 2.0
        assert first["model"] == "model-a" and first["user_id"] == "u1@example.test"
        first_start = datetime.datetime(2026, 1, 1, 10, 0, 1, tzinfo=datetime.timezone.utc)
        assert first["observed_at"] == int(first_start.timestamp())
        second = by_id["0000000000000002"]
        assert (second["tokens_input"], second["tokens_total"], second["cost_usd"]) == (200, 240, 0.25)
        assert second["user_id"] == ""

    def test_rows_from_every_page_are_inserted_in_one_call(self):
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor="c1"), make_v2_page([make_v2_row(2)])])
        ledger = _run_sync(fake)
        assert ledger.bulk_insert_ignore.call_count == 1
        assert len(ledger.bulk_insert_ignore.call_args.args[0]) == 2

    def test_missing_usage_and_cost_reach_the_ledger_as_zero_tokens_and_no_cost(self):
        row = make_v2_row(1)
        del row["usageDetails"], row["totalCost"]
        inserted = _run_sync(FakeLangfuse([make_v2_page([row])])).bulk_insert_ignore.call_args.args[0]
        assert inserted[0]["tokens_total"] == 0 and inserted[0]["cost_usd"] is None and inserted[0]["cost_eur"] is None

    def test_cost_string_reaches_the_ledger_as_float(self):
        row = make_v2_row(1, totalCost="0.020292499999")
        inserted = _run_sync(FakeLangfuse([make_v2_page([row])])).bulk_insert_ignore.call_args.args[0]
        assert inserted[0]["cost_usd"] == 0.020292499999

    def test_error_on_a_later_page_inserts_nothing(self):
        """Rows from earlier pages are not written when a later page fails."""
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor="c1"), _response({}, status=500)])
        import open_webui.tasks.billing as tasks_mod

        ledger = MagicMock()
        with patch("open_webui.models.usage_ledger.UsageLedgerDB", ledger), \
             patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=2.0), \
             patch(GET, side_effect=fake.get):
            with pytest.raises(requests.HTTPError):
                tasks_mod._sync_observations(SINCE)
        ledger.bulk_insert_ignore.assert_not_called()

    def test_repeated_cursor_inserts_nothing(self):
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor="c1"), make_v2_page([make_v2_row(2)], cursor="c1")])
        import open_webui.tasks.billing as tasks_mod

        ledger = MagicMock()
        with patch("open_webui.models.usage_ledger.UsageLedgerDB", ledger), \
             patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=2.0), \
             patch(GET, side_effect=fake.get):
            with pytest.raises(RuntimeError, match="repeated cursor"):
                tasks_mod._sync_observations(SINCE)
        ledger.bulk_insert_ignore.assert_not_called()


class TestRealSnapshots:
    """Recorded staging responses; skipped unless LANGFUSE_SNAPSHOT_DIR is set."""

    def test_cursor_loop_on_two_recorded_pages(self, snapshot_dir):
        """The first recorded page's cursor is sent unchanged and the second page ends the loop."""
        page1 = _load_snapshot(snapshot_dir, "v2_page1.json")
        page2 = _load_snapshot(snapshot_dir, "v2_page2.json")
        fake = FakeLangfuse([page1, page2])
        results = _fetch(fake)
        assert [r["id"] for r in results] == [r["id"] for r in page1["data"] + page2["data"]]
        assert len(fake.observation_calls) == 2
        assert "cursor" not in fake.params(0)
        assert fake.params(1)["cursor"] == page1["meta"]["cursor"]
        assert "cursor" not in page2["meta"]

    def test_recorded_pages_have_no_repeated_ids_or_cursors(self, snapshot_dir):
        page1 = _load_snapshot(snapshot_dir, "v2_page1.json")
        page2 = _load_snapshot(snapshot_dir, "v2_page2.json")
        ids = [r["id"] for r in page1["data"] + page2["data"]]
        assert len(ids) == len(set(ids))
        assert isinstance(page1["meta"]["cursor"], str) and page1["meta"]["cursor"]

    def test_mapped_rows_equal_the_recorded_v1_rows(self, snapshot_dir):
        """For the same ids, mapped v2 rows equal the v1 rows on every field the ledger sync reads."""
        v1 = {r["id"]: r for r in _load_snapshot(snapshot_dir, "v1_observations.json")["data"]}
        v2_rows = _load_snapshot(snapshot_dir, "v2_observations.json")["data"]
        assert {r["id"] for r in v2_rows} == set(v1)
        for row in v2_rows:
            old = v1[row["id"]]
            mapped = _to_v1_shape(row)
            for key in ("id", "traceId", "name", "model", "startTime"):
                assert mapped[key] == old[key], key
            for key in ("input", "output", "total"):
                assert mapped["usage"][key] == old["usage"][key], f"usage.{key}"
            assert mapped["calculatedTotalCost"] == old["calculatedTotalCost"]
