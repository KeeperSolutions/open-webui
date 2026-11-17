"""Shape contract for GET /api/public/v2/observations.

The tests build synthetic v2 and v1 responses with the key set and value types of
real staging responses requested with type=GENERATION and fields=core,basic,model,usage.
No recorded data is stored in the repository.

The `real_snapshot` tests run the same validator on recorded responses. They read
v1_observations.json and v2_observations.json from the directory named by
LANGFUSE_SNAPSHOT_DIR and are skipped when the variable is not set.
"""

import copy
import datetime
import json
import os
from pathlib import Path

import pytest

REQUIRED_STR = ("id", "traceId", "name", "model", "startTime", "userId")
REQUIRED_USAGE_KEYS = ("input", "output", "total")
V1_ONLY_META_KEYS = ("page", "totalPages", "totalItems")
UNREQUESTED_GROUP_KEYS = ("input", "output", "metadata")


####################
# Builders
####################


def make_v2_row(n: int = 1, **overrides) -> dict:
    """One v2 GENERATION row with every key a real v2 row carries."""
    row = {
        "id": f"{n:016x}",
        "traceId": f"{n:032x}",
        "startTime": f"2026-01-01T10:00:{n:02d}.250Z",
        "endTime": f"2026-01-01T10:00:{n:02d}.250Z",
        "projectId": "proj-synthetic",
        "parentObservationId": None,
        "type": "GENERATION",
        "name": "llm_response",
        "level": "DEFAULT",
        "statusMessage": "",
        "version": "",
        "environment": "default",
        "model": "model-a",
        "internalModelId": "00000000-0000-0000-0000-000000000001",
        "modelParameters": {},
        "usageDetails": {"input": 100 * n, "output": 20 * n, "total": 120 * n},
        "inputUsage": 100 * n,
        "outputUsage": 20 * n,
        "totalUsage": 120 * n,
        "costDetails": {"input": 0.001 * n, "output": 0.002 * n, "total": 0.003 * n},
        "inputCost": 0.001 * n,
        "outputCost": 0.002 * n,
        "totalCost": 0.003 * n,
        "usagePricingTierId": "00000000-0000-0000-0000-000000000001_tier_default",
        "usagePricingTierName": "Standard",
        "latency": 0,
        "timeToFirstToken": None,
        "userId": f"user-{n}@example.test",
        "sessionId": f"00000000-0000-0000-0000-{n:012d}",
        "isRootObservation": True,
        "bookmarked": False,
        "public": False,
        "modelId": "00000000-0000-0000-0000-000000000001",
        "inputPrice": "0.00000125",
        "outputPrice": "0.00001",
        "totalPrice": None,
    }
    row.update(overrides)
    return row


def make_v2_page(rows: list | None = None, cursor: str | None = None) -> dict:
    """A v2 response. meta.cursor is present only when a cursor is given."""
    if rows is None:
        rows = [make_v2_row(n) for n in (1, 2, 3)]
    return {"data": rows, "meta": {"cursor": cursor} if cursor is not None else {}}


def make_v1_row(n: int = 1, **overrides) -> dict:
    """One v1 row with the key set of a real v1 observation (no userId, no totalCost)."""
    row = {
        "id": f"{n:016x}",
        "traceId": f"{n:032x}",
        "startTime": f"2026-01-01T10:00:{n:02d}.250Z",
        "endTime": f"2026-01-01T10:00:{n:02d}.250Z",
        "createdAt": f"2026-01-01T10:00:{n:02d}.300Z",
        "updatedAt": f"2026-01-01T10:00:{n:02d}.300Z",
        "completionStartTime": None,
        "projectId": "proj-synthetic",
        "parentObservationId": None,
        "type": "GENERATION",
        "name": "llm_response",
        "level": "DEFAULT",
        "statusMessage": "",
        "version": "",
        "environment": "default",
        "model": "model-a",
        "modelId": "00000000-0000-0000-0000-000000000001",
        "modelParameters": {},
        "input": None,
        "output": None,
        "metadata": {},
        "usage": {"unit": "TOKENS", "input": 100 * n, "output": 20 * n, "total": 120 * n},
        "usageDetails": {"input": 100 * n, "output": 20 * n, "total": 120 * n},
        "unit": "TOKENS",
        "promptTokens": 100 * n,
        "completionTokens": 20 * n,
        "totalTokens": 120 * n,
        "costDetails": {"input": 0.001 * n, "output": 0.002 * n, "total": 0.003 * n},
        "calculatedInputCost": 0.001 * n,
        "calculatedOutputCost": 0.002 * n,
        "calculatedTotalCost": 0.003 * n,
        "inputPrice": 0.00000125,
        "outputPrice": 0.00001,
        "totalPrice": 0,
        "usagePricingTierId": "00000000-0000-0000-0000-000000000001_tier_default",
        "usagePricingTierName": "Standard",
        "latency": 0,
        "timeToFirstToken": None,
        "promptId": None,
        "promptName": None,
        "promptVersion": None,
    }
    row.update(overrides)
    return row


def make_v1_page(rows: list | None = None) -> dict:
    """A v1 response: page-based meta and a top-level _deprecation object."""
    if rows is None:
        rows = [make_v1_row(n) for n in (1, 2, 3)]
    deprecation = {"sunsetAt": "2026-11-16", "replacement": "GET /api/public/v2/observations"}
    return {
        "data": rows,
        "meta": {
            "page": 1,
            "limit": 100,
            "totalItems": len(rows),
            "totalPages": 1,
            "deprecation": dict(deprecation),
        },
        "_deprecation": {**deprecation, "message": "synthetic", "docsUrl": "https://example.test/docs"},
    }


####################
# Validator
####################


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parses_like_ledger(start_time: str) -> bool:
    """Same normalisation the ledger poller applies to startTime."""
    try:
        ts = start_time.rstrip("Z").split(".")[0]
        datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        return True
    except Exception:
        return False


def v2_violations(body) -> list[str]:
    """Return one message per way `body` differs from the v2 shape the poller needs.

    Messages name paths and problems only, never values, so they are safe to print for real data.
    """
    if not isinstance(body, dict):
        return ["body: not an object"]
    problems: list[str] = []

    meta = body.get("meta")
    if not isinstance(meta, dict):
        problems.append("meta: missing or not an object")
    else:
        for key in V1_ONLY_META_KEYS:
            if key in meta:
                problems.append(f"meta.{key}: v1 pagination key present")
        cursor = meta.get("cursor")
        if "cursor" in meta and cursor is not None and not (isinstance(cursor, str) and cursor):
            problems.append("meta.cursor: present but not a non-empty string")

    data = body.get("data")
    if not isinstance(data, list) or not data:
        problems.append("data: missing, not a list, or empty")
        return problems

    seen_ids: set = set()
    for i, row in enumerate(data):
        where = f"data[{i}]"
        if not isinstance(row, dict):
            problems.append(f"{where}: not an object")
            continue
        for key in REQUIRED_STR:
            value = row.get(key)
            if not isinstance(value, str) or not value:
                problems.append(f"{where}.{key}: missing or not a non-empty string")
        if isinstance(row.get("startTime"), str) and not _parses_like_ledger(row["startTime"]):
            problems.append(f"{where}.startTime: not parseable as YYYY-MM-DDTHH:MM:SS[.fff]Z")
        if row.get("type") != "GENERATION":
            problems.append(f"{where}.type: not GENERATION")
        usage = row.get("usageDetails")
        if not isinstance(usage, dict):
            problems.append(f"{where}.usageDetails: missing or not an object")
        else:
            for key in REQUIRED_USAGE_KEYS:
                if not isinstance(usage.get(key), int) or isinstance(usage.get(key), bool):
                    problems.append(f"{where}.usageDetails.{key}: missing or not an integer")
        if not _is_number(row.get("totalCost")):
            problems.append(f"{where}.totalCost: missing or not a number")
        for key in UNREQUESTED_GROUP_KEYS:
            if key in row:
                problems.append(f"{where}.{key}: belongs to a group the poller does not request")
        if row.get("id") in seen_ids:
            problems.append(f"{where}.id: duplicate")
        seen_ids.add(row.get("id"))
    return problems


####################
# Builders and validator against synthetic data
####################


def test_synthetic_v2_page_matches_contract():
    """A page from the builder has every key and type the poller reads."""
    assert v2_violations(make_v2_page()) == []


def test_synthetic_v2_rows_are_distinct_and_well_formed():
    """Builder rows differ per n and keep the 16-hex id and email-like userId forms."""
    rows = [make_v2_row(n) for n in (1, 2, 3)]
    assert len({r["id"] for r in rows}) == 3
    assert all(len(r["id"]) == 16 and int(r["id"], 16) >= 0 for r in rows)
    assert all(r["userId"].endswith("@example.test") for r in rows)


def test_v1_page_is_rejected():
    """The v1 shape differs in pagination keys, userId and totalCost, so the check must fail on it."""
    joined = "\n".join(v2_violations(make_v1_page()))
    assert "meta.totalPages" in joined
    assert "meta.page:" in joined
    assert "meta.totalItems" in joined
    assert "data[0].userId" in joined
    assert "data[0].totalCost" in joined


def test_v1_page_carries_the_fields_the_old_poller_reads():
    """The v1 builder keeps usage and calculatedTotalCost, which v2 rows do not have."""
    row = make_v1_page()["data"][0]
    assert row["usage"] == {"unit": "TOKENS", "input": 100, "output": 20, "total": 120}
    assert "calculatedTotalCost" in row and "userId" not in row and "totalCost" not in row
    assert "usage" not in make_v2_row() and "calculatedTotalCost" not in make_v2_row()


def _drop(key):
    def mutate(body):
        for row in body["data"]:
            row.pop(key, None)

    return mutate


def _set(key, value):
    def mutate(body):
        body["data"][0][key] = value

    return mutate


def _drop_usage_key(key):
    def mutate(body):
        body["data"][0]["usageDetails"].pop(key)

    return mutate


def _add_meta(key, value):
    def mutate(body):
        body["meta"][key] = value

    return mutate


MUTANTS = [
    ("no_id", _drop("id"), "data[0].id"),
    ("no_traceId", _drop("traceId"), "data[0].traceId"),
    ("no_name", _drop("name"), "data[0].name"),
    ("no_model", _drop("model"), "data[0].model"),
    ("no_startTime", _drop("startTime"), "data[0].startTime"),
    ("no_userId", _drop("userId"), "data[0].userId"),
    ("empty_userId", _set("userId", ""), "data[0].userId"),
    ("null_userId", _set("userId", None), "data[0].userId"),
    ("no_usageDetails", _drop("usageDetails"), "data[0].usageDetails"),
    ("usageDetails_not_object", _set("usageDetails", [305, 410]), "data[0].usageDetails"),
    ("usageDetails_no_input", _drop_usage_key("input"), "usageDetails.input"),
    ("usageDetails_no_output", _drop_usage_key("output"), "usageDetails.output"),
    ("usageDetails_no_total", _drop_usage_key("total"), "usageDetails.total"),
    ("no_totalCost", _drop("totalCost"), "data[0].totalCost"),
    ("totalCost_string", _set("totalCost", "0.0045"), "data[0].totalCost"),
    ("totalCost_null", _set("totalCost", None), "data[0].totalCost"),
    ("startTime_with_offset", _set("startTime", "2026-11-16T16:31:18+00:00"), "data[0].startTime"),
    ("startTime_epoch", _set("startTime", "1763310678"), "data[0].startTime"),
    ("type_not_generation", _set("type", "SPAN"), "data[0].type"),
    ("io_group_present", _set("input", "prompt text"), "data[0].input"),
    ("duplicate_id", lambda b: b["data"][1].update(id=b["data"][0]["id"]), "duplicate"),
    ("meta_missing", lambda b: b.pop("meta"), "meta"),
    ("meta_has_totalPages", _add_meta("totalPages", 1), "meta.totalPages"),
    ("meta_cursor_empty", _add_meta("cursor", ""), "meta.cursor"),
    ("meta_cursor_not_string", _add_meta("cursor", 7), "meta.cursor"),
    ("data_empty", lambda b: b.update(data=[]), "data:"),
]


@pytest.mark.parametrize("name,mutate,expected", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutated_synthetic_page_is_rejected(name, mutate, expected):
    """Each single-field corruption of a builder page is reported against that field."""
    body = make_v2_page()
    mutate(body)
    problems = v2_violations(body)
    assert any(expected in p for p in problems), f"{name}: {problems}"


@pytest.mark.parametrize(
    "cursor",
    [None, "opaque-cursor-token"],
    ids=["cursor_absent", "cursor_string"],
)
def test_cursor_is_optional_and_may_be_a_string(cursor):
    """meta.cursor is absent on the last page and a string when more pages exist."""
    assert v2_violations(make_v2_page(cursor=cursor)) == []


def test_cursor_null_is_accepted():
    """A null meta.cursor means no further pages, the same as an absent one."""
    body = make_v2_page()
    body["meta"]["cursor"] = None
    assert v2_violations(body) == []


def test_make_v2_page_sets_cursor_only_when_given():
    """The builder omits meta.cursor on the last page, as the real endpoint does."""
    assert "cursor" not in make_v2_page()["meta"]
    assert make_v2_page(cursor="abc")["meta"] == {"cursor": "abc"}


####################
# Recorded responses (optional)
####################


@pytest.fixture(scope="module")
def snapshot_dir() -> Path:
    raw = os.environ.get("LANGFUSE_SNAPSHOT_DIR")
    if not raw:
        pytest.skip("LANGFUSE_SNAPSHOT_DIR is not set; recorded Langfuse responses are not available")
    path = Path(raw).expanduser()
    if not path.is_dir():
        pytest.skip(f"LANGFUSE_SNAPSHOT_DIR does not point to a directory: {path}")
    return path


def _load_snapshot(snapshot_dir: Path, name: str) -> dict:
    path = snapshot_dir / name
    if not path.is_file():
        pytest.skip(f"{name} not found in LANGFUSE_SNAPSHOT_DIR")
    return json.loads(path.read_text())


@pytest.fixture
def real_v2(snapshot_dir) -> dict:
    return _load_snapshot(snapshot_dir, "v2_observations.json")


@pytest.fixture
def real_v1(snapshot_dir) -> dict:
    return _load_snapshot(snapshot_dir, "v1_observations.json")


def _types(row: dict) -> dict:
    return {k: type(v).__name__ for k, v in row.items()}


def test_real_snapshot_v2_matches_contract(real_v2):
    """A recorded v2 response has every key and type the poller reads."""
    assert v2_violations(real_v2) == []


def test_real_snapshot_v1_is_rejected(real_v1):
    """A recorded v1 response is rejected by the v2 contract."""
    joined = "\n".join(v2_violations(real_v1))
    assert "meta.totalPages" in joined and "data[0].userId" in joined and "data[0].totalCost" in joined


def test_real_snapshot_ids_are_the_same_in_v1_and_v2(real_v1, real_v2):
    """v1 and v2 return the same observation ids for the same window, in the same 16-hex form."""
    v1_ids = {r["id"] for r in real_v1["data"]}
    v2_ids = {r["id"] for r in real_v2["data"]}
    assert v1_ids == v2_ids
    assert all(len(i) == 16 and int(i, 16) >= 0 for i in v2_ids)


def test_real_snapshot_v1_and_v2_agree_on_usage_and_cost(real_v1, real_v2):
    """For shared ids, v1 usage and calculatedTotalCost equal v2 usageDetails and totalCost."""
    v1 = {r["id"]: r for r in real_v1["data"]}
    for row in real_v2["data"]:
        old = v1[row["id"]]
        for key in REQUIRED_USAGE_KEYS:
            assert old["usage"][key] == row["usageDetails"][key], f"usage.{key}"
        assert old["calculatedTotalCost"] == row["totalCost"], "cost"


def test_real_snapshot_start_times_match_between_versions(real_v1, real_v2):
    """startTime strings are identical in v1 and v2 for shared ids."""
    v1 = {r["id"]: r for r in real_v1["data"]}
    assert all(v1[r["id"]]["startTime"] == r["startTime"] for r in real_v2["data"])


def test_builders_have_the_key_set_and_types_of_recorded_rows(real_v1, real_v2):
    """The synthetic rows do not drift from the recorded ones in keys or value types."""
    assert _types(make_v2_row()) == _types(real_v2["data"][0])
    # Sanitized snapshots may omit the content keys, so they are allowed to be missing.
    assert set(real_v1["data"][0]) | set(UNREQUESTED_GROUP_KEYS) == set(make_v1_row())
    assert set(make_v1_page()["meta"]) == set(real_v1["meta"]) | {"deprecation"}


@pytest.mark.parametrize("name,mutate,expected", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutated_real_snapshot_is_rejected(real_v2, name, mutate, expected):
    """Each single-field corruption of a recorded v2 response is reported against that field."""
    body = copy.deepcopy(real_v2)
    mutate(body)
    problems = v2_violations(body)
    assert any(expected in p for p in problems), f"{name}: {problems}"
