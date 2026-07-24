import datetime as dt
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

_LANGFUSE_ENV_VARS = ["LANGFUSE_PK", "LANGFUSE_SK", "LANGFUSE_HOST", "PK", "SK", "HOST"]


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
