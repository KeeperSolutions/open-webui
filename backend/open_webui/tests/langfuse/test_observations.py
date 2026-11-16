"""Tests for langfuse/observations.py: the v2 observations client and its row mapping.

The HTTP layer is replaced by FakeLangfuse, which serves synthetic v2 pages built with
make_v2_row/make_v2_page. The tests marked real snapshot read recorded responses from the
directory named by LANGFUSE_SNAPSHOT_DIR and are skipped when it is not set.
"""
import copy
import datetime
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
import requests

import open_webui.langfuse.observations as obs_module
from open_webui.langfuse.observations import _to_v1_shape, _user_id_from_row, fetch_observations_since
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
    """Serves queued v2 pages and records every request. Any other URL fails the test."""

    def __init__(self, pages: list):
        self.pages = list(pages)
        self.calls: list = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == V2_URL:
            item = self.pages.pop(0)
            if isinstance(item, Exception):
                raise item
            return item if isinstance(item, MagicMock) else _response(item)
        raise AssertionError(f"unexpected url: {url}")

    @property
    def observation_calls(self) -> list:
        return [(u, k) for u, k in self.calls if u == V2_URL]

    def params(self, i: int) -> dict:
        return self.observation_calls[i][1]["params"]

    @property
    def urls(self) -> list:
        return [u for u, _ in self.calls]


def _fetch(fake: FakeLangfuse, since: datetime.datetime = SINCE) -> list:
    with patch(GET, side_effect=fake.get):
        return list(fetch_observations_since(since))


@pytest.fixture(autouse=True)
def patch_environment():
    with patch("open_webui.langfuse.observations.load_env", return_value=("pk", "sk", "https://host")), \
         patch("open_webui.langfuse.observations._now_utc", return_value=NOW), \
         patch("open_webui.langfuse.ledger_guard.ledger_ids_between", return_value=set()):
        yield


def _reset_module_state():
    for cache in (obs_module._missing_user_id_seen, obs_module._bad_cost_seen, obs_module._bad_start_time_seen):
        cache.clear()
    obs_module._missing_user_id_total = 0
    obs_module._bad_cost_total = 0
    obs_module._bad_start_time_total = 0
    obs_module._rate_limit_giveups_total = 0
    obs_module._usage_group_missing_total = 0
    obs_module.reset_fetch_stats()


@pytest.fixture(autouse=True)
def reset_module_state():
    _reset_module_state()
    yield
    _reset_module_state()


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


class TestUserIdFromRow:
    def test_user_id_is_taken_from_the_row(self):
        """The userId of each observation row is returned as is."""
        rows = [make_v2_row(1, userId="alice@example.test"), make_v2_row(2, userId="bob@example.test")]
        results = _fetch(FakeLangfuse([make_v2_page(rows)]))
        assert [r["userId"] for r in results] == ["alice@example.test", "bob@example.test"]

    def test_only_the_observations_endpoint_is_called(self):
        """No request goes to /api/public/traces; FakeLangfuse fails on any other URL."""
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor="c1"), make_v2_page([make_v2_row(2)])])
        _fetch(fake)
        assert set(fake.urls) == {V2_URL} and len(fake.urls) == 2

    def test_row_user_id_is_used_even_when_traces_differ(self):
        """Rows of the same trace keep their own userId; nothing is looked up per trace."""
        trace = make_v2_row(1)["traceId"]
        rows = [make_v2_row(1, userId="a@example.test"), make_v2_row(2, traceId=trace, userId="b@example.test")]
        assert [r["userId"] for r in _fetch(FakeLangfuse([make_v2_page(rows)]))] == ["a@example.test", "b@example.test"]

    @pytest.mark.parametrize("value", [None, "", 0, 7, ["a@example.test"], {"id": "x"}], ids=repr)
    def test_missing_or_invalid_user_id_becomes_empty_string_with_warning_and_count(self, value, caplog):
        """A userId that is null, empty or not a string yields "", one warning and one count."""
        row = make_v2_row(1, userId=value)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            results = _fetch(FakeLangfuse([make_v2_page([row])]))
        assert results[0]["userId"] == ""
        warnings = [r for r in caplog.records if "observation without userId" in r.getMessage()]
        assert len(warnings) == 1 and warnings[0].levelname == "WARNING"
        assert obs_module._missing_user_id_total == 1

    def test_absent_user_id_key_is_handled_like_a_missing_value(self, caplog):
        row = make_v2_row(1)
        del row["userId"]
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            results = _fetch(FakeLangfuse([make_v2_page([row])]))
        assert results[0]["userId"] == "" and obs_module._missing_user_id_total == 1
        assert "observation without userId" in caplog.text

    def test_a_missing_user_id_does_not_drop_or_change_the_rest_of_the_row(self):
        row = make_v2_row(1, userId=None)
        out = _fetch(FakeLangfuse([make_v2_page([row])]))[0]
        assert {k: v for k, v in out.items() if k != "userId"} == _to_v1_shape(row)

    def test_warning_names_the_observation_and_the_running_total(self, caplog):
        rows = [make_v2_row(1, userId=None), make_v2_row(2, userId="")]
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page(rows)]))
        messages = [r.getMessage() for r in caplog.records if "without userId" in r.getMessage()]
        assert "observation=0000000000000001" in messages[0] and "since start: 1" in messages[0]
        assert "observation=0000000000000002" in messages[1] and "since start: 2" in messages[1]

    def test_the_same_observation_is_reported_once_across_fetches(self, caplog):
        """Overlapping poll windows re-read rows, so a known observation does not warn or count again."""
        row = make_v2_row(1, userId=None)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page([row])]))
            _fetch(FakeLangfuse([make_v2_page([row])]))
        assert caplog.text.count("observation without userId") == 1
        assert obs_module._missing_user_id_total == 1

    def test_a_different_observation_is_reported_again(self, caplog):
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page([make_v2_row(1, userId=None)])]))
            _fetch(FakeLangfuse([make_v2_page([make_v2_row(2, userId=None)])]))
        assert caplog.text.count("observation without userId") == 2
        assert obs_module._missing_user_id_total == 2

    def test_a_row_without_a_usable_id_is_counted_every_time(self, caplog):
        """Without an id there is nothing to remember, so each read warns and counts."""
        row = make_v2_row(1, userId=None)
        del row["id"]
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page([row])]))
            _fetch(FakeLangfuse([make_v2_page([row])]))
        assert caplog.text.count("observation without userId") == 2
        assert obs_module._missing_user_id_total == 2

    def test_rows_with_a_user_id_neither_warn_nor_count_nor_log_the_user(self, caplog):
        rows = [make_v2_row(1, userId="alice@example.test"), make_v2_row(2, userId="bob@example.test")]
        with caplog.at_level("DEBUG", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page(rows)]))
        assert obs_module._missing_user_id_total == 0
        assert "without userId" not in caplog.text and "example.test" not in caplog.text

    def test_counter_and_seen_set_start_empty_in_a_new_process(self):
        """A freshly imported module has counted nothing yet (the autouse fixture resets the shared one)."""
        spec = importlib.util.spec_from_file_location("observations_fresh", obs_module.__file__)
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        assert fresh._missing_user_id_total == 0
        assert len(fresh._missing_user_id_seen) == 0
        assert (fresh._bad_cost_total, fresh._bad_start_time_total) == (0, 0)
        assert (fresh._rate_limit_giveups_total, fresh._usage_group_missing_total) == (0, 0)
        assert fresh.last_fetch_count() is None

    def test_counter_is_exact_under_concurrent_use(self):
        """Eight threads reporting distinct observations do not lose counts."""
        rows = [make_v2_row(n, userId=None) for n in range(1, 201)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(_user_id_from_row, rows)) == [""] * 200
        assert obs_module._missing_user_id_total == 200
        assert len(obs_module._missing_user_id_seen) == 200


def _run_sync(fake: FakeLangfuse, deep_rescan: bool = False):
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
        tasks_mod._sync_observations(SINCE, deep_rescan=deep_rescan)
    return ledger


class TestLedgerSyncReadsMappedRows:
    def test_values_reach_the_ledger_rows(self):
        """Tokens, cost, model, time and user in the inserted rows equal the values in the v2 rows."""
        rows = [make_v2_row(1, totalCost=0.5, userId="u1@example.test"), make_v2_row(2, totalCost=0.25, userId=None)]
        fake = FakeLangfuse([make_v2_page(rows)])
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
        """A row without usage fields among rows that have them is stored as before."""
        bare = make_v2_row(1)
        del bare["usageDetails"], bare["totalCost"]
        page = make_v2_page([bare, make_v2_row(2)])
        inserted = _run_sync(FakeLangfuse([page])).bulk_insert_ignore.call_args.args[0]
        stored = {r["langfuse_observation_id"]: r for r in inserted}["0000000000000001"]
        assert stored["tokens_total"] == 0 and stored["cost_usd"] is None and stored["cost_eur"] is None

    def test_cost_string_reaches_the_ledger_as_float(self):
        row = make_v2_row(1, totalCost="0.020292499999")
        inserted = _run_sync(FakeLangfuse([make_v2_page([row])])).bulk_insert_ignore.call_args.args[0]
        assert inserted[0]["cost_usd"] == 0.020292499999

    def test_missing_user_id_is_inserted_as_empty_string(self):
        """A row without a userId is still inserted, with user_id "" as before."""
        row = make_v2_row(1, userId=None)
        inserted = _run_sync(FakeLangfuse([make_v2_page([row])])).bulk_insert_ignore.call_args.args[0]
        assert len(inserted) == 1 and inserted[0]["user_id"] == ""

    def test_deep_rescan_passes_user_ids_to_the_backfill(self):
        """The nightly rescan hands the same rows, with user ids from the v2 rows, to the user-id backfill."""
        rows = [make_v2_row(1, userId="u1@example.test"), make_v2_row(2, userId=None)]
        ledger = _run_sync(FakeLangfuse([make_v2_page(rows)]), deep_rescan=True)
        backfilled = ledger.bulk_upsert_user_ids.call_args.args[0]
        assert [(r["langfuse_observation_id"], r["user_id"]) for r in backfilled] == [
            ("0000000000000001", "u1@example.test"),
            ("0000000000000002", ""),
        ]

    def test_hot_path_does_not_run_the_backfill(self):
        ledger = _run_sync(FakeLangfuse([make_v2_page([make_v2_row(1)])]))
        ledger.bulk_upsert_user_ids.assert_not_called()

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


class TestUsageGroupDriftGuard:
    def _bare(self, n: int) -> dict:
        row = make_v2_row(n)
        del row["usageDetails"], row["totalCost"]
        return row

    def test_raises_when_no_row_has_usage_fields(self, caplog):
        """A result where every row lacks usageDetails and totalCost means the group was not returned."""
        fake = FakeLangfuse([make_v2_page([self._bare(1), self._bare(2)])])
        with caplog.at_level("ERROR", logger="open_webui.langfuse.observations"):
            expected = r"^Langfuse v2 response has no usage fields on any of 2 observations$"
            with pytest.raises(RuntimeError, match=expected):
                _fetch(fake)
        assert obs_module._usage_group_missing_total == 1
        assert "no usage fields on any of 2 observations (occurrences since start: 1)" in caplog.text

    def test_counts_rows_over_all_pages(self):
        fake = FakeLangfuse([make_v2_page([self._bare(1)], cursor="c1"), make_v2_page([self._bare(2), self._bare(3)])])
        with pytest.raises(RuntimeError, match="any of 3 observations"):
            _fetch(fake)

    def test_one_row_with_usage_fields_is_enough(self):
        assert len(_fetch(FakeLangfuse([make_v2_page([self._bare(1), make_v2_row(2)])]))) == 2

    @pytest.mark.parametrize("keep", ["usageDetails", "totalCost"])
    def test_either_key_alone_is_enough(self, keep):
        row = self._bare(1)
        row[keep] = {} if keep == "usageDetails" else None
        assert len(_fetch(FakeLangfuse([make_v2_page([row])]))) == 1

    def test_empty_result_does_not_raise(self):
        assert _fetch(FakeLangfuse([make_v2_page([])])) == []
        assert obs_module._usage_group_missing_total == 0

    def test_the_request_asks_for_the_usage_group(self):
        """The guard is only meaningful while the usage group is requested."""
        fake = FakeLangfuse([make_v2_page([])])
        _fetch(fake)
        assert "usage" in fake.params(0)["fields"].split(",")

    def test_nothing_is_inserted_when_the_guard_raises(self):
        import open_webui.tasks.billing as tasks_mod

        ledger = MagicMock()
        fake = FakeLangfuse([make_v2_page([self._bare(1), self._bare(2)])])
        with patch("open_webui.models.usage_ledger.UsageLedgerDB", ledger), \
             patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=2.0), \
             patch(GET, side_effect=fake.get):
            with pytest.raises(RuntimeError):
                tasks_mod._sync_observations(SINCE)
        ledger.bulk_insert_ignore.assert_not_called()


BAD_START_TIMES = [
    "2026-11-16T16:31:18+00:00",
    "1763310678",
    "",
    "not a time",
    "2026-13-45T99:00:00Z",
    "2026-01-01T10:00:01X",
]


class TestUnusableValueReports:
    def test_non_numeric_cost_warns_with_counts_and_keeps_the_value(self, caplog):
        """The report names counts only; the row is returned unchanged so the ledger result is the same."""
        rows = [make_v2_row(1, totalCost="n/a"), make_v2_row(2), make_v2_row(3)]
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            results = _fetch(FakeLangfuse([make_v2_page(rows)]))
        assert results[0]["calculatedTotalCost"] == "n/a"
        assert "1 of 3 observations have a cost that is not a number" in caplog.text
        assert "total since start: 1" in caplog.text
        assert obs_module._bad_cost_total == 1 and obs_module._bad_start_time_total == 0

    @pytest.mark.parametrize("cost", [None, 0, 0.5, "0.25", "5e-06"])
    def test_numbers_and_null_cost_do_not_warn(self, cost, caplog):
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page([make_v2_row(1, totalCost=cost)])]))
        assert "not a number" not in caplog.text and obs_module._bad_cost_total == 0

    @pytest.mark.parametrize("value", BAD_START_TIMES + [None, 7, ["x"]], ids=repr)
    def test_unparseable_start_time_warns_with_counts_and_keeps_the_value(self, value, caplog):
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            results = _fetch(FakeLangfuse([make_v2_page([make_v2_row(1, startTime=value), make_v2_row(2)])]))
        assert results[0]["startTime"] == value
        assert "1 of 2 observations have a startTime that cannot be parsed" in caplog.text
        assert obs_module._bad_start_time_total == 1 and obs_module._bad_cost_total == 0

    def test_absent_start_time_warns(self, caplog):
        row = make_v2_row(1)
        del row["startTime"]
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page([row])]))
        assert "cannot be parsed" in caplog.text and obs_module._bad_start_time_total == 1

    @pytest.mark.parametrize(
        "value",
        ["2026-01-01T10:00:01.250Z", "2026-01-01T10:00:01Z", "2026-01-01T10:00:01.250123Z"],
    )
    def test_valid_start_times_do_not_warn(self, value, caplog):
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page([make_v2_row(1, startTime=value)])]))
        assert "cannot be parsed" not in caplog.text and obs_module._bad_start_time_total == 0

    def test_the_same_observation_is_counted_once_across_fetches(self, caplog):
        row = make_v2_row(1, totalCost="n/a", startTime="")
        with caplog.at_level("WARNING", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page([row])]))
            _fetch(FakeLangfuse([make_v2_page([row])]))
        assert caplog.text.count("not a number") == 1 and caplog.text.count("cannot be parsed") == 1
        assert obs_module._bad_cost_total == 1 and obs_module._bad_start_time_total == 1

    def test_reports_contain_no_ids_or_user_ids(self, caplog):
        row = make_v2_row(1, totalCost="n/a", startTime="", userId="someone@example.test")
        with caplog.at_level("DEBUG", logger="open_webui.langfuse.observations"):
            _fetch(FakeLangfuse([make_v2_page([row])]))
        messages = [r.getMessage() for r in caplog.records]
        reports = "\n".join(m for m in messages if "not a number" in m or "cannot be parsed" in m)
        assert reports and row["id"] not in reports and "example.test" not in reports and row["traceId"] not in reports

    @pytest.mark.parametrize("value", BAD_START_TIMES + ["2026-01-01T10:00:01.250Z", "2026-01-01T10:00:01Z"], ids=repr)
    def test_start_time_check_agrees_with_what_the_ledger_sync_does(self, value):
        """_start_time_parses is true exactly when the ledger sync keeps the row's own time."""
        fallback = 1_900_000_000
        row = make_v2_row(1, startTime=value)
        with patch("open_webui.tasks.billing.time.time", return_value=fallback):
            inserted = _run_sync(FakeLangfuse([make_v2_page([row])])).bulk_insert_ignore.call_args.args[0]
        used_fallback = inserted[0]["observed_at"] == fallback
        assert used_fallback == (not obs_module._start_time_parses(row))

    def test_values_stored_in_the_ledger_are_unchanged_by_the_report(self):
        """A bad cost is stored as no cost and a bad startTime as the current time, as without the report."""
        row = make_v2_row(1, totalCost="n/a", startTime="")
        with patch("open_webui.tasks.billing.time.time", return_value=1_900_000_000):
            inserted = _run_sync(FakeLangfuse([make_v2_page([row])])).bulk_insert_ignore.call_args.args[0]
        assert inserted[0]["cost_usd"] is None and inserted[0]["cost_eur"] is None
        assert inserted[0]["observed_at"] == 1_900_000_000


class TestRateLimitCap:
    def _limited(self, n: int) -> list:
        return [_response({}, status=429, headers={"Retry-After": "3"}) for _ in range(n)]

    def test_five_rate_limited_responses_then_success_returns_the_rows(self):
        fake = FakeLangfuse(self._limited(5) + [make_v2_page([make_v2_row(1)])])
        with patch("open_webui.langfuse.observations.time.sleep") as sleep:
            assert len(_fetch(fake)) == 1
        assert sleep.call_count == 5 and obs_module._rate_limit_giveups_total == 0

    def test_a_sixth_consecutive_429_raises(self, caplog):
        fake = FakeLangfuse(self._limited(6) + [make_v2_page([make_v2_row(1)])])
        with patch("open_webui.langfuse.observations.time.sleep") as sleep, \
             caplog.at_level("ERROR", logger="open_webui.langfuse.observations"):
            with pytest.raises(RuntimeError, match=r"^Langfuse rate limit: gave up after 5 retries \(page 1\)$"):
                _fetch(fake)
        assert sleep.call_count == 5 and len(fake.observation_calls) == 6
        assert obs_module._rate_limit_giveups_total == 1
        assert "giving up on page 1 after 5 retries (give-ups since start: 1)" in caplog.text

    def test_the_retry_count_starts_again_after_a_successful_page(self):
        pages = (
            self._limited(5)
            + [make_v2_page([make_v2_row(1)], cursor="c1")]
            + self._limited(5)
            + [make_v2_page([make_v2_row(2)])]
        )
        with patch("open_webui.langfuse.observations.time.sleep") as sleep:
            assert len(_fetch(FakeLangfuse(pages))) == 2
        assert sleep.call_count == 10

    def test_the_failing_page_is_reported(self):
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor="c1")] + self._limited(6))
        with patch("open_webui.langfuse.observations.time.sleep"):
            with pytest.raises(RuntimeError, match=r"\(page 2\)$"):
                _fetch(fake)

    def test_nothing_is_inserted_when_the_fetch_gives_up(self):
        import open_webui.tasks.billing as tasks_mod

        ledger = MagicMock()
        fake = FakeLangfuse([make_v2_page([make_v2_row(1)], cursor="c1")] + self._limited(6))
        with patch("open_webui.models.usage_ledger.UsageLedgerDB", ledger), \
             patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=2.0), \
             patch("open_webui.langfuse.observations.time.sleep"), \
             patch(GET, side_effect=fake.get):
            with pytest.raises(RuntimeError):
                tasks_mod._sync_observations(SINCE)
        ledger.bulk_insert_ignore.assert_not_called()


class TestTickLog:
    """The sync wrapper logs counts of fetched, inserted and ignored observations."""

    def _rows(self) -> list:
        return [make_v2_row(n, userId=f"user-{n}@example.test") for n in (1, 2, 3)]

    def _sync(self, pages: list, inserted: int, deep_rescan: bool | None = None, fetch: bool = True):
        """Run _sync_and_log with a sync that reads the real fetch and reports `inserted` new rows.

        Returns the value from _sync_and_log and the keyword arguments the sync received.
        """
        import open_webui.tasks.billing as tasks_mod

        received: list = []

        def sync(since, *, deep_rescan=False):
            received.append((since, deep_rescan))
            if fetch:
                list(fetch_observations_since(since))
            return inserted

        kwargs = {} if deep_rescan is None else {"deep_rescan": deep_rescan}
        with patch.object(tasks_mod, "_sync_observations", sync), patch(GET, side_effect=FakeLangfuse(pages).get):
            return tasks_mod._sync_and_log(SINCE, **kwargs), received

    def test_logs_fetched_inserted_and_ignored(self, caplog):
        with caplog.at_level("INFO", logger="open_webui.tasks.billing"):
            self._sync([make_v2_page(self._rows())], inserted=1)
        assert "[ledger-poller] poll: fetched=3 inserted=1 ignored=2" in caplog.text

    def test_everything_inserted_means_nothing_ignored(self, caplog):
        with caplog.at_level("INFO", logger="open_webui.tasks.billing"):
            self._sync([make_v2_page(self._rows())], inserted=3)
        assert "poll: fetched=3 inserted=3 ignored=0" in caplog.text

    def test_rescan_is_labelled_and_the_flag_is_passed_on(self, caplog):
        with caplog.at_level("INFO", logger="open_webui.tasks.billing"):
            _, received = self._sync([make_v2_page(self._rows())], inserted=3, deep_rescan=True)
        assert "rescan: fetched=3 inserted=3 ignored=0" in caplog.text
        assert received == [(SINCE, True)]

    def test_the_line_has_counts_only(self, caplog):
        with caplog.at_level("INFO", logger="open_webui.tasks.billing"):
            self._sync([make_v2_page(self._rows())], inserted=3)
        line = next(r.getMessage() for r in caplog.records if "fetched=" in r.getMessage())
        assert "example.test" not in line and "0000000000000001" not in line

    def test_an_empty_fetch_is_logged_as_zero(self, caplog):
        with caplog.at_level("INFO", logger="open_webui.tasks.billing"):
            self._sync([make_v2_page([])], inserted=0)
        assert "poll: fetched=0 inserted=0 ignored=0" in caplog.text

    def test_returns_the_inserted_count_and_defaults_to_the_poll_path(self, caplog):
        with caplog.at_level("INFO", logger="open_webui.tasks.billing"):
            result, received = self._sync([make_v2_page(self._rows())], inserted=1)
        assert result == 1
        assert received == [(SINCE, False)]
        assert "poll: fetched=3 inserted=1 ignored=2" in caplog.text

    def test_ignored_is_never_negative(self, caplog):
        with caplog.at_level("INFO", logger="open_webui.tasks.billing"):
            self._sync([make_v2_page(self._rows())], inserted=5)
        assert "inserted=5 ignored=0" in caplog.text

    def test_a_sync_that_fetches_nothing_does_not_reuse_the_previous_count(self, caplog):
        """The count of an earlier sync in the same thread is cleared before the next one starts."""
        self._sync([make_v2_page(self._rows())], inserted=3)
        assert obs_module.last_fetch_count() == 3
        with caplog.at_level("INFO", logger="open_webui.tasks.billing"):
            caplog.clear()
            self._sync([], inserted=0, fetch=False)
        assert "fetched=" not in caplog.text

    def test_the_poller_and_the_nightly_rescan_use_the_wrapper(self):
        import inspect

        import open_webui.tasks.billing as tasks_mod

        assert "_sync_and_log" in inspect.getsource(tasks_mod.periodic_ledger_poller)
        assert "_sync_and_log" in inspect.getsource(tasks_mod.periodic_nightly_deep_rescan)


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

    def test_recorded_rows_carry_a_user_id(self, snapshot_dir):
        """Every recorded v2 row yields a non-empty userId and none is reported as missing."""
        pages = [_load_snapshot(snapshot_dir, name) for name in ("v2_page1.json", "v2_page2.json")]
        results = _fetch(FakeLangfuse(pages))
        assert len(results) == 4
        assert all(isinstance(r["userId"], str) and r["userId"] for r in results)
        assert obs_module._missing_user_id_total == 0

    def test_recorded_user_ids_come_from_the_rows(self, snapshot_dir):
        """The userId returned for each recorded row equals the userId stored in that row."""
        body = _load_snapshot(snapshot_dir, "v2_observations.json")
        results = _fetch(FakeLangfuse([body]))
        assert [r["userId"] for r in results] == [row["userId"] for row in body["data"]]
