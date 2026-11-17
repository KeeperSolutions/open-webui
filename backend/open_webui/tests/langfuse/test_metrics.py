import datetime as dt
import json
from unittest.mock import patch
import pytest

from open_webui.langfuse.metrics import (
    _isoformat_utc,
    last_day_fixed_window,
    last_week_fixed_window,
    last_month_fixed_window,
    custom_days_fixed_window,
    today_utc_window_now,
    current_month_window,
    parse_rows,
    auth_header,
)


# ---------- _isoformat_utc ----------

def test_isoformat_utc_format():
    d = dt.datetime(2024, 3, 15, 10, 30, 45)
    assert _isoformat_utc(d) == "2024-03-15T10:30:45Z"


def test_isoformat_utc_zero_padded():
    d = dt.datetime(2024, 1, 5, 1, 2, 3)
    assert _isoformat_utc(d) == "2024-01-05T01:02:03Z"


# ---------- time windows ----------

def test_last_day_fixed_window_range():
    start, end = last_day_fixed_window()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    end_dt = dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
    delta = end_dt - start_dt
    assert delta == dt.timedelta(hours=23, minutes=59, seconds=59)


def test_last_day_fixed_window_start_midnight():
    start, _ = last_day_fixed_window()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    assert start_dt.hour == 0
    assert start_dt.minute == 0
    assert start_dt.second == 0


def test_last_week_fixed_window_seven_days():
    start, end = last_week_fixed_window()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    end_dt = dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
    delta = end_dt - start_dt
    assert delta == dt.timedelta(days=7) - dt.timedelta(seconds=1)


def test_last_week_starts_on_monday():
    start, _ = last_week_fixed_window()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    assert start_dt.weekday() == 0  # Monday


def test_last_month_fixed_window_starts_first():
    start, _ = last_month_fixed_window()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    assert start_dt.day == 1
    assert start_dt.hour == 0


def test_last_month_fixed_window_ends_last_day_of_prev_month():
    _, end = last_month_fixed_window()
    end_dt = dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
    # end should be 23:59:59 of last day of previous month
    assert end_dt.hour == 23
    assert end_dt.minute == 59
    assert end_dt.second == 59


def test_custom_days_fixed_window_correct_span():
    start, end = custom_days_fixed_window(7)
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    end_dt = dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
    delta = end_dt - start_dt
    assert delta == dt.timedelta(days=7) - dt.timedelta(seconds=1)


def test_custom_days_raises_on_zero():
    with pytest.raises(ValueError):
        custom_days_fixed_window(0)


def test_custom_days_raises_on_negative():
    with pytest.raises(ValueError):
        custom_days_fixed_window(-5)


def test_today_utc_window_start_is_midnight():
    start, _ = today_utc_window_now()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    assert start_dt.hour == 0
    assert start_dt.minute == 0
    assert start_dt.second == 0


def test_today_utc_window_end_after_start():
    start, end = today_utc_window_now()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    end_dt = dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
    assert end_dt >= start_dt


def test_current_month_window_starts_first():
    start, _ = current_month_window()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    assert start_dt.day == 1
    assert start_dt.hour == 0


def test_current_month_window_end_after_start():
    start, end = current_month_window()
    start_dt = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    end_dt = dt.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
    assert end_dt >= start_dt


# ---------- parse_rows ----------

def test_parse_rows_normal():
    raw = [{"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": "100", "sum_totalCost": "0.002"}]
    result = parse_rows(raw)
    assert len(result) == 1
    assert result[0]["user"] == "a@b.com"
    assert result[0]["model"] == "gpt-4"
    assert result[0]["tokens"] == 100
    assert result[0]["cost"] == pytest.approx(0.002)


def test_parse_rows_filters_zero_tokens_and_cost():
    raw = [{"userId": "a@b.com", "providedModelName": None, "sum_totalTokens": "0", "sum_totalCost": "0"}]
    assert parse_rows(raw) == []


def test_parse_rows_filters_rounding_noise():
    # Sub-epsilon cost with zero tokens is API noise, should be filtered
    raw = [{"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": "0", "sum_totalCost": "1e-12"}]
    assert parse_rows(raw) == []


def test_parse_rows_keeps_meaningful_small_cost():
    # Cost above epsilon with zero tokens should be kept (e.g. image generation)
    raw = [{"userId": "a@b.com", "providedModelName": "dall-e", "sum_totalTokens": "0", "sum_totalCost": "0.001"}]
    result = parse_rows(raw)
    assert len(result) == 1


def test_parse_rows_keeps_zero_tokens_with_cost():
    raw = [{"userId": "a@b.com", "providedModelName": "img-gen", "sum_totalTokens": "0", "sum_totalCost": "0.05"}]
    result = parse_rows(raw)
    assert len(result) == 1
    assert result[0]["tokens"] == 0
    assert result[0]["cost"] == pytest.approx(0.05)


def test_parse_rows_unknown_user_fallback():
    raw = [{"userId": None, "providedModelName": "gpt-4", "sum_totalTokens": "10", "sum_totalCost": "0.001"}]
    result = parse_rows(raw)
    assert result[0]["user"] == "(unknown)"


def test_parse_rows_unknown_model_fallback():
    raw = [{"userId": "a@b.com", "providedModelName": None, "sum_totalTokens": "10", "sum_totalCost": "0.001"}]
    result = parse_rows(raw)
    assert result[0]["model"] == "Unknown Model"


def test_parse_rows_null_values_treated_as_zero():
    raw = [{"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": None, "sum_totalCost": None}]
    assert parse_rows(raw) == []


def test_parse_rows_missing_keys():
    raw = [{}]
    assert parse_rows(raw) == []


def test_parse_rows_multiple_rows():
    raw = [
        {"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": "100", "sum_totalCost": "0.01"},
        {"userId": "b@b.com", "providedModelName": None, "sum_totalTokens": "0", "sum_totalCost": "0"},
        {"userId": "c@b.com", "providedModelName": "claude", "sum_totalTokens": "200", "sum_totalCost": "0.02"},
    ]
    result = parse_rows(raw)
    assert len(result) == 2
    assert result[0]["user"] == "a@b.com"
    assert result[1]["user"] == "c@b.com"


# ---------- auth_header ----------

def test_auth_header_format():
    import base64
    header = auth_header("pk-123", "sk-456")
    expected = base64.b64encode(b"pk-123:sk-456").decode()
    assert header == {"Authorization": f"Basic {expected}"}


def test_auth_header_empty_credentials():
    # Empty creds should still produce a valid Basic header, not crash
    header = auth_header("", "")
    assert header["Authorization"].startswith("Basic ")


def test_auth_header_special_chars_in_credentials():
    # Colons and special chars in keys should not break base64 encoding
    header = auth_header("pk:with:colons", "sk/with+special==")
    assert "Authorization" in header
    assert header["Authorization"].startswith("Basic ")


# ---------- parse_rows: data type edge cases ----------

def test_parse_rows_tokens_as_string_integer():
    # Langfuse returns numbers as strings
    raw = [{"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": "999", "sum_totalCost": "0.1"}]
    result = parse_rows(raw)
    assert result[0]["tokens"] == 999
    assert isinstance(result[0]["tokens"], int)


def test_parse_rows_cost_as_string_float():
    raw = [{"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": "10", "sum_totalCost": "0.00123456"}]
    result = parse_rows(raw)
    assert isinstance(result[0]["cost"], float)


def test_parse_rows_tokens_as_actual_integer():
    # Some API versions may return actual int, not string
    raw = [{"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": 500, "sum_totalCost": 0.05}]
    result = parse_rows(raw)
    assert result[0]["tokens"] == 500


def test_parse_rows_very_large_token_count():
    raw = [{"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": "999999999", "sum_totalCost": "999.9999"}]
    result = parse_rows(raw)
    assert result[0]["tokens"] == 999999999


def test_parse_rows_empty_string_user_uses_fallback():
    raw = [{"userId": "", "providedModelName": "gpt-4", "sum_totalTokens": "10", "sum_totalCost": "0.01"}]
    result = parse_rows(raw)
    assert result[0]["user"] == "(unknown)"


def test_parse_rows_empty_string_model_uses_fallback():
    raw = [{"userId": "a@b.com", "providedModelName": "", "sum_totalTokens": "10", "sum_totalCost": "0.01"}]
    result = parse_rows(raw)
    assert result[0]["model"] == "Unknown Model"


def test_parse_rows_empty_list():
    assert parse_rows([]) == []


def test_parse_rows_all_zero_rows_filtered():
    raw = [
        {"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": "0", "sum_totalCost": "0"},
        {"userId": "b@b.com", "providedModelName": "claude", "sum_totalTokens": "0", "sum_totalCost": "0"},
    ]
    assert parse_rows(raw) == []


def test_parse_rows_negative_cost_kept():
    # A refund/credit could theoretically produce negative cost — should not be filtered
    raw = [{"userId": "a@b.com", "providedModelName": "gpt-4", "sum_totalTokens": "100", "sum_totalCost": "-0.05"}]
    result = parse_rows(raw)
    assert len(result) == 1
    assert result[0]["cost"] < 0


# ---------- time window boundary: month boundaries ----------

def test_last_month_window_is_contiguous_with_current_month():
    _, last_month_end = last_month_fixed_window()
    current_month_start, _ = current_month_window()
    end_dt = dt.datetime.strptime(last_month_end, "%Y-%m-%dT%H:%M:%SZ")
    start_dt = dt.datetime.strptime(current_month_start, "%Y-%m-%dT%H:%M:%SZ")
    # end of last month + 1 second == start of this month
    assert end_dt + dt.timedelta(seconds=1) == start_dt


def test_last_day_window_is_contiguous_with_today():
    _, last_day_end = last_day_fixed_window()
    today_start, _ = today_utc_window_now()
    end_dt = dt.datetime.strptime(last_day_end, "%Y-%m-%dT%H:%M:%SZ")
    start_dt = dt.datetime.strptime(today_start, "%Y-%m-%dT%H:%M:%SZ")
    assert end_dt + dt.timedelta(seconds=1) == start_dt


def test_custom_days_1_equals_yesterday_window():
    custom_start, custom_end = custom_days_fixed_window(1)
    last_day_start, last_day_end = last_day_fixed_window()
    assert custom_start == last_day_start
    assert custom_end == last_day_end


# ---------- build_metrics_query ----------

def test_build_metrics_query_structure():
    from open_webui.langfuse.metrics import build_metrics_query
    q = build_metrics_query("2024-01-01T00:00:00Z", "2024-01-31T23:59:59Z")
    assert q["view"] == "observations"
    assert q["fromTimestamp"] == "2024-01-01T00:00:00Z"
    assert q["toTimestamp"] == "2024-01-31T23:59:59Z"
    assert any(m["measure"] == "totalTokens" for m in q["metrics"])
    assert any(m["measure"] == "totalCost" for m in q["metrics"])
    assert any(d["field"] == "userId" for d in q["dimensions"])
    assert any(d["field"] == "providedModelName" for d in q["dimensions"])


# ---------- load_env fallbacks ----------

_LANGFUSE_ENV_VARS = [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_PK",
    "LANGFUSE_SK",
    "LANGFUSE_HOST",
    "PK",
    "SK",
    "HOST",
]


@pytest.fixture
def clean_langfuse_env(monkeypatch):
    for var in _LANGFUSE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    with patch("open_webui.langfuse.metrics.load_dotenv"):
        yield


def test_load_env_uses_langfuse_pk_over_pk(clean_langfuse_env, monkeypatch):
    from open_webui.langfuse.metrics import load_env
    monkeypatch.setenv("LANGFUSE_PK", "langfuse-pk")
    monkeypatch.setenv("PK", "fallback-pk")
    pk, _, _ = load_env()
    assert pk == "langfuse-pk"


def test_load_env_falls_back_to_pk(clean_langfuse_env, monkeypatch):
    from open_webui.langfuse.metrics import load_env
    monkeypatch.setenv("PK", "fallback-pk")
    pk, _, _ = load_env()
    assert pk == "fallback-pk"


def test_load_env_default_host(clean_langfuse_env):
    from open_webui.langfuse.metrics import load_env
    _, _, host = load_env()
    assert host == "https://cloud.langfuse.com"


def test_load_env_custom_host(clean_langfuse_env, monkeypatch):
    from open_webui.langfuse.metrics import load_env
    monkeypatch.setenv("LANGFUSE_HOST", "https://my-langfuse.internal")
    _, _, host = load_env()
    assert host == "https://my-langfuse.internal"


# ---------- observation count ----------

def test_parse_rows_includes_observation_count():
    from open_webui.langfuse.metrics import parse_rows
    raw = [
        {"userId": "u1", "providedModelName": "gpt-4",
         "sum_totalTokens": 100, "sum_totalCost": 0.5, "count_count": 7},
    ]
    out = parse_rows(raw)
    assert out == [
        {"user": "u1", "model": "gpt-4", "tokens": 100, "cost": 0.5, "observations": 7},
    ]


def test_build_metrics_query_requests_count():
    from open_webui.langfuse.metrics import build_metrics_query
    q = build_metrics_query("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    measures = {(m["measure"], m["aggregation"]) for m in q["metrics"]}
    assert ("count", "count") in measures
    assert ("totalCost", "sum") in measures  # existing measures preserved


# ---------- period window envelope ----------

def _fake_fetch(rows):
    """Stub for fetch_metrics; the getters only pass its result to parse_rows."""
    return lambda host, headers, query: rows


@pytest.mark.parametrize(
    "getter_name,window_fn",
    [
        ("get_today_so_far", today_utc_window_now),
        ("get_last_day", last_day_fixed_window),
        ("get_last_week", last_week_fixed_window),
        ("get_last_month", last_month_fixed_window),
        ("get_current_month", current_month_window),
    ],
)
def test_getters_return_the_window_they_queried(getter_name, window_fn):
    """The reported window must be the one actually sent to Langfuse, so a UI
    cannot disagree with the data it labels."""
    from open_webui.langfuse import metrics as m

    seen = {}

    def spy(host, headers, query):
        seen["from"] = query["fromTimestamp"]
        seen["to"] = query["toTimestamp"]
        return []

    with patch.object(m, "load_env", return_value=("pk", "sk", "https://h")), \
         patch.object(m, "fetch_metrics", side_effect=spy):
        from_ts, to_ts, rows = getattr(m, getter_name)()

    expected_from, expected_to = window_fn()
    assert (from_ts, to_ts) == (seen["from"], seen["to"])
    assert from_ts == expected_from
    assert to_ts == expected_to
    assert rows == []


def test_get_custom_days_returns_its_window():
    from open_webui.langfuse import metrics as m

    with patch.object(m, "load_env", return_value=("pk", "sk", "https://h")), \
         patch.object(m, "fetch_metrics", return_value=[]):
        from_ts, to_ts, rows = m.get_custom_days(7)

    assert (from_ts, to_ts) == custom_days_fixed_window(7)
    assert rows == []


def test_getters_pass_rows_through_parse_rows():
    from open_webui.langfuse import metrics as m

    raw = [{"userId": "u1", "providedModelName": "gpt-4",
            "sum_totalTokens": 10, "sum_totalCost": 0.25, "count_count": 3}]
    with patch.object(m, "load_env", return_value=("pk", "sk", "https://h")), \
         patch.object(m, "fetch_metrics", return_value=raw):
        _, _, rows = m.get_last_week()

    assert rows == [{"user": "u1", "model": "gpt-4", "tokens": 10,
                     "cost": 0.25, "observations": 3}]


def test_metrics_response_serialises_from_as_json_key():
    """`from` is a Python keyword; the model must still emit it as the JSON key."""
    from open_webui.routers.langfuse import MetricsResponse

    payload = MetricsResponse(
        **{"from": "2026-07-01T00:00:00Z", "to": "2026-07-31T23:59:59Z", "rows": []}
    ).model_dump(by_alias=True)
    assert payload["from"] == "2026-07-01T00:00:00Z"
    assert payload["to"] == "2026-07-31T23:59:59Z"
    assert payload["rows"] == []


# ---------- Langfuse v2 metrics: query rules, request and response ----------

_V2_VIEWS = {"observations", "scores-numeric", "scores-categorical", "scores-boolean"}
_V2_AGGREGATIONS = {"sum", "avg", "count", "max", "min", "p50", "p75", "p90", "p95", "p99", "histogram"}
_V2_HIGH_CARDINALITY = {"id", "traceId", "userId", "sessionId", "parentObservationId"}
_V2_MAX_ROW_LIMIT = 1000


def assert_v2_query_accepted(query):
    """Check a query against the rules Langfuse enforces on GET /api/public/v2/metrics.

    A query that groups by a high-cardinality dimension must be a top-N query: a row limit of at most 1000,
    a descending order on one of its metrics, and no time dimension.
    """
    assert query["view"] in _V2_VIEWS
    assert query["metrics"], "at least one metric is required"
    for metric in query["metrics"]:
        assert metric["aggregation"] in _V2_AGGREGATIONS
    assert query["fromTimestamp"] < query["toTimestamp"]
    dimensions = {d["field"] for d in query.get("dimensions", [])}
    if not dimensions & _V2_HIGH_CARDINALITY:
        return
    row_limit = (query.get("config") or {}).get("row_limit")
    assert isinstance(row_limit, int) and 1 <= row_limit <= _V2_MAX_ROW_LIMIT, "row_limit must be 1 to 1000"
    metric_names = {f"{m['aggregation']}_{m['measure']}" for m in query["metrics"]}
    descending = [o for o in query.get("orderBy") or [] if o["direction"] == "desc" and o["field"] in metric_names]
    assert descending, "orderBy must be descending on one of the query's metrics"
    assert not query.get("timeDimension"), "a time dimension cannot be combined with a high-cardinality dimension"


def make_v2_metrics_row(user="u1", model="gpt-4", tokens=100, cost=0.5, count=1):
    return {
        "userId": user,
        "providedModelName": model,
        "sum_totalTokens": tokens,
        "sum_totalCost": cost,
        "count_count": count,
    }


class FakeMetricsResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = {"data": []} if body is None else body
        self.text = text

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            import requests

            raise requests.HTTPError(f"{self.status_code} Client Error: test")


def _fetch(query, response):
    from open_webui.langfuse import metrics as m

    with patch.object(m.requests, "get", return_value=response) as get:
        rows = m.fetch_metrics("https://h", {"Authorization": "Basic x"}, query, timeout=7)
    return rows, get


def _query():
    from open_webui.langfuse.metrics import build_metrics_query

    return build_metrics_query("2026-11-01T00:00:00Z", "2026-11-07T23:59:59Z")


def test_built_query_is_accepted_by_the_top_n_rules():
    assert_v2_query_accepted(_query())


@pytest.mark.parametrize(
    "change",
    [
        lambda q: q.pop("config"),
        lambda q: q["config"].update(row_limit=1001),
        lambda q: q["config"].update(row_limit=0),
        lambda q: q["config"].update(row_limit="1000"),
        lambda q: q.pop("orderBy"),
        lambda q: q["orderBy"][0].update(direction="asc"),
        lambda q: q["orderBy"][0].update(field="userId"),
        lambda q: q.update(timeDimension={"granularity": "day"}),
        lambda q: q.update(view="traces"),
    ],
)
def test_rule_check_rejects_a_query_langfuse_would_refuse(change):
    q = _query()
    change(q)
    with pytest.raises(AssertionError):
        assert_v2_query_accepted(q)


def test_query_orders_by_total_cost_descending_with_the_maximum_row_limit():
    q = _query()
    assert q["orderBy"] == [{"field": "sum_totalCost", "direction": "desc"}]
    assert q["config"] == {"row_limit": 1000}
    assert "timeDimension" not in q


def test_request_goes_to_the_v2_endpoint_with_the_query_as_json():
    q = _query()
    _, get = _fetch(q, FakeMetricsResponse(body={"data": [make_v2_metrics_row()]}))
    args, kwargs = get.call_args
    assert args == ("https://h/api/public/v2/metrics",)
    assert json.loads(kwargs["params"]["query"]) == q
    assert kwargs["headers"] == {"Authorization": "Basic x"}
    assert kwargs["timeout"] == 7


def test_rows_are_returned_from_the_data_key():
    rows, _ = _fetch(_query(), FakeMetricsResponse(body={"data": [make_v2_metrics_row(user="a")]}))
    assert rows == [make_v2_metrics_row(user="a")]


def test_a_response_without_data_gives_no_rows():
    rows, _ = _fetch(_query(), FakeMetricsResponse(body={}))
    assert rows == []


def _rows(count, user_format="secret-user-{}@example.test"):
    return [make_v2_metrics_row(user=user_format.format(i)) for i in range(count)]


@pytest.fixture
def fresh_counter():
    from open_webui.langfuse import metrics as m

    previous = m._truncated_total
    m._truncated_total = 0
    yield m
    m._truncated_total = previous


def test_a_result_below_the_row_limit_is_returned(caplog):
    with caplog.at_level("DEBUG", logger="open_webui.langfuse.metrics"):
        rows, _ = _fetch(_query(), FakeMetricsResponse(body={"data": _rows(999)}))
    assert len(rows) == 999
    assert caplog.text == ""


@pytest.mark.parametrize("count", [1000, 1001])
def test_a_result_at_or_above_the_row_limit_raises(count):
    from open_webui.langfuse.metrics import MetricsTruncated

    with pytest.raises(MetricsTruncated) as exc:
        _fetch(_query(), FakeMetricsResponse(body={"data": _rows(count)}))
    assert str(exc.value) == (
        f"Langfuse metrics returned {count} rows, the row limit of 1000; the result may be incomplete."
    )


def test_the_row_limit_comes_from_the_query_not_from_a_constant():
    from open_webui.langfuse.metrics import MetricsTruncated

    q = _query()
    q["config"]["row_limit"] = 5
    rows, _ = _fetch(q, FakeMetricsResponse(body={"data": _rows(4)}))
    assert len(rows) == 4
    with pytest.raises(MetricsTruncated, match="5 rows, the row limit of 5"):
        _fetch(q, FakeMetricsResponse(body={"data": _rows(5)}))


def test_the_log_names_the_returned_count_and_the_limit_separately(caplog, fresh_counter):
    from open_webui.langfuse.metrics import MetricsTruncated

    q = _query()
    q["config"]["row_limit"] = 5
    with caplog.at_level("ERROR", logger="open_webui.langfuse.metrics"):
        with pytest.raises(MetricsTruncated):
            _fetch(q, FakeMetricsResponse(body={"data": _rows(7)}))
    assert "returned 7 rows, the row limit of 5, for the window" in caplog.text


def test_a_query_without_a_row_limit_never_raises():
    q = _query()
    q.pop("config")
    rows, _ = _fetch(q, FakeMetricsResponse(body={"data": _rows(1000)}))
    assert len(rows) == 1000


def test_the_error_log_has_counts_and_the_window_but_no_user_ids(caplog, fresh_counter):
    from open_webui.langfuse.metrics import MetricsTruncated

    with caplog.at_level("ERROR", logger="open_webui.langfuse.metrics"):
        with pytest.raises(MetricsTruncated):
            _fetch(_query(), FakeMetricsResponse(body={"data": _rows(1000)}))
    assert caplog.text.count("returned 1000 rows, the row limit of 1000") == 1
    assert "for the window 2026-11-01T00:00:00Z to 2026-11-07T23:59:59Z" in caplog.text
    assert "the result is not used (occurrences since start: 1)" in caplog.text
    assert "secret-user" not in caplog.text and "example.test" not in caplog.text


def test_each_truncated_result_increments_the_counter(caplog, fresh_counter):
    from open_webui.langfuse.metrics import MetricsTruncated

    assert fresh_counter._truncated_total == 0
    for expected in (1, 2, 3):
        with caplog.at_level("ERROR", logger="open_webui.langfuse.metrics"):
            with pytest.raises(MetricsTruncated):
                _fetch(_query(), FakeMetricsResponse(body={"data": _rows(1000)}))
        assert fresh_counter._truncated_total == expected
        assert f"(occurrences since start: {expected})" in caplog.text


def test_a_result_below_the_limit_does_not_touch_the_counter(fresh_counter):
    _fetch(_query(), FakeMetricsResponse(body={"data": _rows(999)}))
    assert fresh_counter._truncated_total == 0


def _admin():
    return type("C", (), {"role": "admin", "id": "a1", "email": "admin@example.test"})()


def _truncating_langfuse():
    from open_webui.langfuse import metrics as m

    return (
        patch.object(m, "load_env", return_value=("pk", "sk", "https://h")),
        patch.object(m.requests, "get", return_value=FakeMetricsResponse(body={"data": _rows(1000)})),
    )


def test_the_metrics_route_answers_502_with_a_general_message_when_the_result_is_truncated(caplog):
    import asyncio

    from fastapi import HTTPException
    from open_webui.routers.langfuse import get_langfuse_metrics

    env, get = _truncating_langfuse()
    with env, get, caplog.at_level("ERROR"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_langfuse_metrics(period="week", days=None, team_id=None, user=_admin(), db=None))

    assert exc.value.status_code == 502
    assert exc.value.detail == (
        "Failed to fetch Langfuse metrics: Langfuse metrics returned 1000 rows, the row limit of 1000; "
        "the result may be incomplete."
    )
    assert "secret-user" not in exc.value.detail and "secret-user" not in caplog.text


def test_the_my_usage_route_also_answers_502_when_the_result_is_truncated():
    import asyncio

    from fastapi import HTTPException
    from open_webui.routers.langfuse import get_my_langfuse_usage

    env, get = _truncating_langfuse()
    with env, get:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_my_langfuse_usage(user=_admin()))

    assert exc.value.status_code == 502
    assert exc.value.detail.startswith("Failed to fetch Langfuse usage: Langfuse metrics returned 1000 rows")
    assert "secret-user" not in exc.value.detail


@pytest.mark.parametrize("status", [400, 401, 410, 429, 500])
def test_an_error_status_raises(status):
    import requests

    with pytest.raises(requests.HTTPError, match=str(status)):
        _fetch(_query(), FakeMetricsResponse(status_code=status, text="x"))


def test_an_error_response_body_is_logged_truncated(caplog):
    text = "High cardinality dimension(s) 'userId' require both 'config.row_limit' and 'orderBy'." + "z" * 500
    with caplog.at_level("ERROR", logger="open_webui.langfuse.metrics"):
        with pytest.raises(Exception):
            _fetch(_query(), FakeMetricsResponse(status_code=400, text=text))
    assert "failed: 400 High cardinality dimension(s) 'userId'" in caplog.text
    assert text[:300] in caplog.text
    assert text[:301] not in caplog.text


def test_a_successful_response_logs_no_error(caplog):
    with caplog.at_level("DEBUG", logger="open_webui.langfuse.metrics"):
        _fetch(_query(), FakeMetricsResponse(body={"data": [make_v2_metrics_row()]}))
    assert caplog.text == ""


def test_parse_rows_handles_the_v2_row_shapes():
    raw = [
        make_v2_metrics_row(user="a", model="gpt-4", tokens=10, cost=0, count=2),
        make_v2_metrics_row(user="a", model=None, tokens=0, cost=0, count=4),
        make_v2_metrics_row(user="b", model="m", tokens=5, cost=None, count=1),
    ]
    assert parse_rows(raw) == [
        {"user": "a", "model": "gpt-4", "tokens": 10, "cost": 0.0, "observations": 2},
        {"user": "b", "model": "m", "tokens": 5, "cost": 0.0, "observations": 1},
    ]


def test_a_period_getter_sends_a_valid_v2_query_and_returns_parsed_rows():
    from open_webui.langfuse import metrics as m

    response = FakeMetricsResponse(body={"data": [make_v2_metrics_row(user="a", tokens=7, cost=1, count=3)]})
    with patch.object(m, "load_env", return_value=("pk", "sk", "https://h")), \
         patch.object(m.requests, "get", return_value=response) as get:
        from_ts, to_ts, rows = m.get_last_week()

    sent = json.loads(get.call_args.kwargs["params"]["query"])
    assert_v2_query_accepted(sent)
    assert (sent["fromTimestamp"], sent["toTimestamp"]) == (from_ts, to_ts)
    assert get.call_args.args == ("https://h/api/public/v2/metrics",)
    assert rows == [{"user": "a", "model": "gpt-4", "tokens": 7, "cost": 1.0, "observations": 3}]


def test_the_request_timeout_defaults_to_30_seconds():
    from open_webui.langfuse import metrics as m

    with patch.object(m.requests, "get", return_value=FakeMetricsResponse()) as get:
        m.fetch_metrics("https://h", {}, _query())
    assert get.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize("count", [{}, {"count_count": None}])
def test_a_row_without_an_observation_count_reports_zero(count):
    raw = [{"userId": "u", "providedModelName": "m", "sum_totalTokens": 5, "sum_totalCost": 0.1, **count}]
    assert parse_rows(raw)[0]["observations"] == 0
