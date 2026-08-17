"""
Tests for the team PII masking policy resolver.

Scope: reading the policy out of `group.permissions` and memoizing it per
request. NO enforcement — wiring the resolved value into the inlet is covered
further down, under "Enforcement through process_pipeline_inlet_filter".

These tests deliberately exercise the REAL `has_permission` from
utils/access_control, mocking only `Groups.get_groups_by_member_id`. Mocking
`has_permission` itself would make the multi-group merge untestable — and the
merge is exactly where a mis-named key silently flips fail-closed to fail-open.
"""

import asyncio
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# stripe is an optional billing dependency not installed in the test environment.
# Mock it before any open_webui import triggers the import chain.
sys.modules.setdefault("stripe", MagicMock())

from open_webui.utils.access_control import has_permission, has_permission_for_groups
from open_webui.config import PII_MASKING_ENFORCED_PERMISSION
from open_webui.routers.pipelines import (
    PiiMaskingUnavailableError,
    process_pipeline_inlet_filter,
    resolve_pii_masking_enforced,
)

GROUPS_LOOKUP = "open_webui.utils.access_control.Groups.get_groups_by_member_id"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id="u1", role="user"):
    return SimpleNamespace(id=user_id, role=role)


def _make_request(default_enforced=False):
    """Fake Request carrying app.state.config.USER_PERMISSIONS and a bare state.

    `default_enforced` models the operator-set env default
    (USER_PERMISSIONS_CHAT_PII_MASKING_ENFORCED), which is what lands in
    app.state.config.USER_PERMISSIONS.

    A fresh dict per request matters: has_permission calls
    fill_missing_permissions, which mutates the mapping it is handed.
    """
    config = SimpleNamespace(
        USER_PERMISSIONS={"chat": {"pii_masking_enforced": default_enforced}}
    )
    return SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(config=config)),
    )


def _group(permissions):
    return SimpleNamespace(permissions=permissions)


def _enforced(permissions_value):
    return _group({"chat": {"pii_masking_enforced": permissions_value}})


# ---------------------------------------------------------------------------
# Key naming / multi-group merge — the fail-closed boundary
# ---------------------------------------------------------------------------


def test_single_group_enforcing_returns_true():
    with patch(GROUPS_LOOKUP, return_value=[_enforced(True)]):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is True


def test_two_groups_one_enforcing_one_without_the_key_returns_true():
    """THE key-naming test.

    One group enforces, the other does not carry the key at all. The merge is
    `permissions[key] or value` — most permissive wins. Because the key means
    "masking is mandatory", that OR resolves to STRICTEST wins. If the key were
    ever renamed to mean "user may switch masking off", this exact case would
    start returning False and the policy would be bypassable by joining any
    unconfigured group.
    """
    groups = [_group({"chat": {"temporary_enforced": False}}), _enforced(True)]
    with patch(GROUPS_LOOKUP, return_value=groups):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is True


def test_two_groups_false_and_true_returns_true():
    with patch(GROUPS_LOOKUP, return_value=[_enforced(False), _enforced(True)]):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is True


def test_enforcing_group_first_still_returns_true():
    """Order must not matter; a lax group listed later must not win."""
    with patch(GROUPS_LOOKUP, return_value=[_enforced(True), _enforced(False)]):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is True


def test_all_groups_false_returns_false():
    with patch(GROUPS_LOOKUP, return_value=[_enforced(False), _enforced(False)]):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is False


# ---------------------------------------------------------------------------
# Absent / malformed data
# ---------------------------------------------------------------------------


def test_user_without_any_group_falls_back_to_env_default_false():
    with patch(GROUPS_LOOKUP, return_value=[]):
        assert (
            resolve_pii_masking_enforced(_make_request(default_enforced=False), _make_user())
            is False
        )


def test_user_without_any_group_honours_env_default_true():
    """Operator can enforce instance-wide; users with no group are covered too."""
    with patch(GROUPS_LOOKUP, return_value=[]):
        assert (
            resolve_pii_masking_enforced(_make_request(default_enforced=True), _make_user())
            is True
        )


def test_group_without_the_key_falls_back_to_default():
    with patch(GROUPS_LOOKUP, return_value=[_group({"chat": {"delete": True}})]):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is False


def test_group_with_null_permissions_falls_back_to_default():
    with patch(GROUPS_LOOKUP, return_value=[_group(None)]):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is False


def test_group_with_traversable_but_wrong_shape_falls_back_to_default():
    """`chat` is a string: membership test is valid Python and yields False."""
    with patch(GROUPS_LOOKUP, return_value=[_group({"chat": "nonsense"})]):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is False


def test_group_with_untraversable_permissions_fails_closed():
    """`chat` is an int: the membership test raises, so we must fail closed."""
    with patch(GROUPS_LOOKUP, return_value=[_group({"chat": 5})]):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is True


# ---------------------------------------------------------------------------
# Fail-closed on lookup failure
# ---------------------------------------------------------------------------


def test_lookup_exception_fails_closed_to_enforced():
    with patch(GROUPS_LOOKUP, side_effect=RuntimeError("db is down")):
        assert resolve_pii_masking_enforced(_make_request(), _make_user()) is True


def test_lookup_exception_is_logged_as_warning(caplog):
    """A silent fail-closed would look like every toggle locking for no reason."""
    with caplog.at_level(logging.WARNING, logger="open_webui.routers.pipelines"):
        with patch(GROUPS_LOOKUP, side_effect=RuntimeError("db is down")):
            resolve_pii_masking_enforced(_make_request(), _make_user())

    assert any(
        "pii_policy" in r.message and "enforced=True" in r.message for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Per-request memo
# ---------------------------------------------------------------------------


def test_second_call_in_same_request_does_not_hit_the_database():
    request = _make_request()
    user = _make_user()
    with patch(GROUPS_LOOKUP, return_value=[_enforced(True)]) as lookup:
        assert resolve_pii_masking_enforced(request, user) is True
        assert resolve_pii_masking_enforced(request, user) is True
        assert lookup.call_count == 1


def test_memoized_false_is_not_recomputed():
    """`in`, not truthiness: a memoized False is still a computed answer."""
    request = _make_request()
    user = _make_user()
    with patch(GROUPS_LOOKUP, return_value=[_enforced(False)]) as lookup:
        assert resolve_pii_masking_enforced(request, user) is False
        assert resolve_pii_masking_enforced(request, user) is False
        assert lookup.call_count == 1


def test_memo_is_keyed_by_user_within_one_request():
    """Two users resolved inside one request must not share an answer."""
    request = _make_request()
    enforced_user = _make_user("enforced-user")
    free_user = _make_user("free-user")

    def by_member(user_id, db=None):
        return [_enforced(True)] if user_id == "enforced-user" else [_enforced(False)]

    with patch(GROUPS_LOOKUP, side_effect=by_member):
        assert resolve_pii_masking_enforced(request, enforced_user) is True
        assert resolve_pii_masking_enforced(request, free_user) is False


def test_memo_does_not_leak_between_requests():
    """Separate requests, different users: the second must resolve its own value."""
    first_request = _make_request()
    second_request = _make_request()

    with patch(GROUPS_LOOKUP, return_value=[_enforced(True)]):
        assert resolve_pii_masking_enforced(first_request, _make_user("u1")) is True

    with patch(GROUPS_LOOKUP, return_value=[_enforced(False)]) as lookup:
        assert resolve_pii_masking_enforced(second_request, _make_user("u2")) is False
        assert lookup.call_count == 1


# ---------------------------------------------------------------------------
# Admins are not exempt
# ---------------------------------------------------------------------------


def test_admin_is_not_exempt_from_the_policy():
    """The sibling `temporary_enforced` permission exempts admins; this one
    must not. If anyone "aligns" this code with that precedent, this test fails.
    """
    with patch(GROUPS_LOOKUP, return_value=[_enforced(True)]):
        admin = resolve_pii_masking_enforced(_make_request(), _make_user("a1", role="admin"))
        member = resolve_pii_masking_enforced(_make_request(), _make_user("u1", role="user"))

    assert admin is True
    assert admin == member


# ---------------------------------------------------------------------------
# Contract guards
# ---------------------------------------------------------------------------


def test_permission_key_is_named_as_a_restriction():
    """Guards the naming rule itself. A key meaning "user may switch
    masking off" would invert the multi-group merge from strictest- to
    loosest-wins without changing a single line of merge code.
    """
    assert PII_MASKING_ENFORCED_PERMISSION == "chat.pii_masking_enforced"


def test_permission_key_resolves_inside_default_permissions():
    """The dotted key must actually address the defaults it is meant to read.

    The key now lives in config.py beside DEFAULT_USER_PERMISSIONS; this walks
    the path to prove the two agree. A key renamed on one side only would leave
    `has_permission` traversing a missing branch, which returns False — the
    fail-OPEN direction — with nothing anywhere reporting a problem.
    """
    from open_webui.config import DEFAULT_USER_PERMISSIONS

    node = DEFAULT_USER_PERMISSIONS
    for part in PII_MASKING_ENFORCED_PERMISSION.split("."):
        assert part in node, f"{PII_MASKING_ENFORCED_PERMISSION} does not address DEFAULT_USER_PERMISSIONS"
        node = node[part]
    assert isinstance(node, bool)


def test_resolver_never_touches_user_settings():
    """The policy is an overlay, never a write."""
    user = _make_user()
    user.settings = {"ui": {"pipelines": {"valves": {"pii_filter": {"pii_masking_enabled": False}}}}}
    before = repr(user.settings)

    with patch(GROUPS_LOOKUP, return_value=[_enforced(True)]):
        assert resolve_pii_masking_enforced(_make_request(), user) is True

    assert repr(user.settings) == before


# ===========================================================================
# Enforcement through process_pipeline_inlet_filter
#
# These drive the real inlet, not the resolver in isolation, and they patch
# only the group lookup — so the policy travels the same path it will in
# production: resolver -> guard (point 1) -> loop (point 2) -> pipeline valves.
# ===========================================================================

PII_FILTER_ID = "pii_filter"


def _inlet_user(stored_pii=None, user_id="user-1", role="user"):
    settings_dict = {}
    if stored_pii is not None:
        settings_dict = {
            "ui": {"pipelines": {"valves": {PII_FILTER_ID: {"pii_masking_enabled": stored_pii}}}}
        }

    class _Settings:
        def model_dump(self):
            return settings_dict

    return SimpleNamespace(
        id=user_id,
        email="test@example.com",
        name="Test User",
        role=role,
        settings=_Settings() if stored_pii is not None else None,
    )


def _inlet_request(default_enforced=False):
    """Inlet request stub with a REAL `state`, so the per-request memo works."""
    request = MagicMock()
    request.app.state.config.OPENAI_API_BASE_URLS = ["http://pipeline-host"]
    request.app.state.config.OPENAI_API_KEYS = ["secret-key"]
    request.app.state.config.USER_PERMISSIONS = {
        "chat": {"pii_masking_enforced": default_enforced}
    }
    request.state = SimpleNamespace()
    return request


def _filter_entry(filter_id, priority=0):
    return {
        "id": filter_id,
        "urlIdx": 0,
        "pipeline": {"type": "filter", "priority": priority, "pipelines": ["*"]},
    }


def _inlet_models(*filter_ids):
    models = {"gpt-4": {"id": "gpt-4"}}
    for i, fid in enumerate(filter_ids):
        models[fid] = _filter_entry(fid, priority=i)
    return models


def _patch_session(captured):
    """Patch aiohttp.ClientSession, capturing each request_data sent to a filter."""

    def _response_cm(request_data):
        resp = MagicMock()
        resp.json = AsyncMock(return_value=request_data["body"])
        resp.raise_for_status = MagicMock()
        resp.content_type = "application/json"
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def _post(url, *, headers, json, ssl):
        captured.append(json)
        return _response_cm(json)

    session = MagicMock()
    session.post = _post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return patch("open_webui.routers.pipelines.aiohttp.ClientSession", return_value=session_cm)


def _patch_session_unreachable():
    """Patch aiohttp.ClientSession so posting to a filter raises — Mechanism 1."""

    def _post(url, *, headers, json, ssl):
        raise OSError("connection refused")

    session = MagicMock()
    session.post = _post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return patch("open_webui.routers.pipelines.aiohttp.ClientSession", return_value=session_cm)


def _valves_for(captured, filter_id=PII_FILTER_ID):
    """The valves the given filter actually received."""
    for call, fid in captured:
        if fid == filter_id:
            return call["user"]["valves"]
    raise AssertionError(f"filter {filter_id} was never called")


def _run_inlet(payload, user, models, request=None, groups=None, groups_side_effect=None):
    """Run the inlet with a controlled policy; return (payload, captured_calls)."""
    request = request or _inlet_request()
    raw = []
    lookup_kwargs = (
        {"side_effect": groups_side_effect}
        if groups_side_effect is not None
        else {"return_value": groups if groups is not None else []}
    )
    with patch(GROUPS_LOOKUP, **lookup_kwargs):
        with _patch_session(raw):
            result = asyncio.run(process_pipeline_inlet_filter(request, payload, user, models))
    # Filters are called in priority order, so zip against the model order.
    ordered = [m for m in models.values() if "pipeline" in m]
    ordered.sort(key=lambda m: m["pipeline"]["priority"])
    return result, list(zip(raw, [m["id"] for m in ordered]))


# --- The silent hole (point 1) ---------------------------------------------


def test_enforced_user_with_toggle_off_is_refused_when_pipeline_is_gone():
    """⚠️ THE silent-hole test.

    Policy ON, the user switched the in-chat toggle OFF, and the PII filter has
    been pruned from the model registry because the pipeline is down. Without
    point 1 the guard reads only the payload, sees False, no-ops; the loop then
    has no filter to iterate and the message leaves UNMASKED with no error.

    This test fails the moment point 1 is removed. It is the only test that does.
    """
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    models = _inlet_models()  # pipeline down: no PII filter resolved

    with patch(GROUPS_LOOKUP, return_value=[_enforced(True)]):
        with pytest.raises(PiiMaskingUnavailableError):
            asyncio.run(
                process_pipeline_inlet_filter(
                    _inlet_request(), payload, _inlet_user(), models
                )
            )


def test_unenforced_user_with_toggle_off_is_not_refused_when_pipeline_is_gone():
    """Mirror image: without a policy, masking OFF must never block chat."""
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    result, _ = _run_inlet(payload, _inlet_user(), _inlet_models(), groups=[])
    assert result is payload


# --- Policy beats the per-conversation override (point 2) ------------------


def test_policy_overrides_in_chat_toggle_off():
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    _, captured = _run_inlet(
        payload, _inlet_user(), _inlet_models(PII_FILTER_ID), groups=[_enforced(True)]
    )
    assert _valves_for(captured)["pii_masking_enabled"] is True


def test_policy_overrides_stored_setting_off_when_no_request_override():
    payload = {"model": "gpt-4"}
    _, captured = _run_inlet(
        payload,
        _inlet_user(stored_pii=False),
        _inlet_models(PII_FILTER_ID),
        groups=[_enforced(True)],
    )
    assert _valves_for(captured)["pii_masking_enabled"] is True


def test_policy_leaves_an_already_on_request_untouched():
    payload = {"model": "gpt-4", "features": {"pii_masking": True}}
    _, captured = _run_inlet(
        payload, _inlet_user(), _inlet_models(PII_FILTER_ID), groups=[_enforced(True)]
    )
    assert _valves_for(captured)["pii_masking_enabled"] is True


def test_admin_under_policy_is_masked_like_anyone_else():
    """Admins are not exempt at the enforcement layer either, not just in the
    resolver."""
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    _, captured = _run_inlet(
        payload,
        _inlet_user(role="admin"),
        _inlet_models(PII_FILTER_ID),
        groups=[_enforced(True)],
    )
    assert _valves_for(captured)["pii_masking_enabled"] is True


# --- Task generators -------------------------------------------------------


def test_policy_applies_to_task_generator_payloads():
    """The eight task endpoints send no `features` at all. Under a policy their
    titles/tags must still be masked, or they leak PII the chat itself masked.
    """
    payload = {
        "model": "gpt-4",
        "metadata": {"task": "title_generation", "chat_id": "c1"},
    }
    _, captured = _run_inlet(
        payload,
        _inlet_user(stored_pii=False),
        _inlet_models(PII_FILTER_ID),
        groups=[_enforced(True)],
    )
    assert _valves_for(captured)["pii_masking_enabled"] is True


# --- P=OFF: pre-existing behaviour must not move ----------------------------


def test_unenforced_request_override_off_still_reaches_pipeline_as_false():
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    _, captured = _run_inlet(
        payload, _inlet_user(), _inlet_models(PII_FILTER_ID), groups=[]
    )
    assert _valves_for(captured)["pii_masking_enabled"] is False


def test_unenforced_stored_setting_off_still_reaches_pipeline_as_false():
    payload = {"model": "gpt-4"}
    _, captured = _run_inlet(
        payload, _inlet_user(stored_pii=False), _inlet_models(PII_FILTER_ID), groups=[]
    )
    assert _valves_for(captured)["pii_masking_enabled"] is False


def test_unenforced_user_without_any_setting_sends_no_valve_key():
    """Absent key means the pipeline applies its own default (ON)."""
    payload = {"model": "gpt-4"}
    _, captured = _run_inlet(
        payload, _inlet_user(), _inlet_models(PII_FILTER_ID), groups=[]
    )
    assert "pii_masking_enabled" not in _valves_for(captured)


# --- Scope: the policy must not widen the pre-existing override leak --------


def test_policy_is_not_applied_to_filters_outside_pii_filter_ids():
    """The per-request override already leaks onto unrelated filters — that is
    pre-existing and untouched. The policy must not add to it.
    """
    payload = {"model": "gpt-4"}
    _, captured = _run_inlet(
        payload,
        _inlet_user(),
        _inlet_models(PII_FILTER_ID, "telemetry_filter"),
        groups=[_enforced(True)],
    )
    assert _valves_for(captured, PII_FILTER_ID)["pii_masking_enabled"] is True
    assert "pii_masking_enabled" not in _valves_for(captured, "telemetry_filter")


# --- Branch 2 (:240) reads the policy-rewritten valve -----------------------


def test_connection_error_under_policy_fails_closed_even_with_toggle_off():
    """Branch 2 is deliberately NOT modified: it reads `per_filter_valves` after
    point 2 has rewritten it. With policy ON and the in-chat toggle OFF, a
    pipeline connection error must still refuse the request. If point 2 ran after
    branch 2 — or not at all — this would silently pass the message through.
    """
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    with patch(GROUPS_LOOKUP, return_value=[_enforced(True)]):
        with _patch_session_unreachable():
            with pytest.raises(PiiMaskingUnavailableError):
                asyncio.run(
                    process_pipeline_inlet_filter(
                        _inlet_request(), payload, _inlet_user(), _inlet_models(PII_FILTER_ID)
                    )
                )


def test_connection_error_without_policy_and_toggle_off_does_not_block():
    """Same failure, no policy: masking was genuinely off, so chat is not blocked."""
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    with patch(GROUPS_LOOKUP, return_value=[]):
        with _patch_session_unreachable():
            result = asyncio.run(
                process_pipeline_inlet_filter(
                    _inlet_request(), payload, _inlet_user(), _inlet_models(PII_FILTER_ID)
                )
            )
    assert result is payload


# --- Fail-closed propagation reaches BOTH points ---------------------------


def test_policy_lookup_failure_reaches_point_two_as_enforced():
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    _, captured = _run_inlet(
        payload,
        _inlet_user(),
        _inlet_models(PII_FILTER_ID),
        groups_side_effect=RuntimeError("db is down"),
    )
    assert _valves_for(captured)["pii_masking_enabled"] is True


def test_policy_lookup_failure_reaches_point_one_as_enforced():
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    with patch(GROUPS_LOOKUP, side_effect=RuntimeError("db is down")):
        with pytest.raises(PiiMaskingUnavailableError):
            asyncio.run(
                process_pipeline_inlet_filter(
                    _inlet_request(), payload, _inlet_user(), _inlet_models()
                )
            )


# --- Memo is shared by both points -----------------------------------------


def test_two_inlet_calls_in_one_request_resolve_the_policy_once():
    """Both points read one memoized value; a second inlet call in the same
    request must not re-query. Without the memo this would be 2 (and 9 per
    message across chat + task generators).
    """
    request = _inlet_request()
    user = _inlet_user()
    models = _inlet_models(PII_FILTER_ID)
    captured = []

    with patch(GROUPS_LOOKUP, return_value=[_enforced(True)]) as lookup:
        with _patch_session(captured):
            asyncio.run(
                process_pipeline_inlet_filter(request, {"model": "gpt-4"}, user, models)
            )
            asyncio.run(
                process_pipeline_inlet_filter(request, {"model": "gpt-4"}, user, models)
            )

    assert lookup.call_count == 1


def test_inlet_never_writes_to_user_settings():
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    user = _inlet_user(stored_pii=False)
    before = repr(user.settings.model_dump())

    _run_inlet(payload, user, _inlet_models(PII_FILTER_ID), groups=[_enforced(True)])

    assert repr(user.settings.model_dump()) == before


# ===========================================================================
# The batched path used by the governance dashboard
#
# `/users/` resolves the policy for a whole page from groups it has already
# fetched. That must never drift from the enforcement path, so both entry
# points are driven with identical input and compared.
# ===========================================================================


def _plain_group(permissions):
    return SimpleNamespace(permissions=permissions)


BATCH_CASES = [
    ([], False, False),
    ([], True, True),
    ([_enforced(True)], False, True),
    ([_enforced(False)], False, False),
    ([_enforced(False), _enforced(True)], False, True),
    ([_enforced(True), _enforced(False)], False, True),
    ([_plain_group({})], False, False),
    ([_plain_group(None)], False, False),
    ([_plain_group({"chat": {"temporary_enforced": True}})], False, False),
]


@pytest.mark.parametrize("groups,env_default,expected", BATCH_CASES)
def test_batched_resolution_matches_has_permission(groups, env_default, expected):
    """One rule, two entry points — they must not diverge.

    `has_permission` delegates to `has_permission_for_groups`, so this is a
    guard against someone re-introducing a second copy of the merge.
    """
    defaults = {"chat": {"pii_masking_enforced": env_default}}

    batched = has_permission_for_groups(
        groups, PII_MASKING_ENFORCED_PERMISSION, json.loads(json.dumps(defaults))
    )
    with patch(GROUPS_LOOKUP, return_value=groups):
        single = has_permission(
            "u1", PII_MASKING_ENFORCED_PERMISSION, json.loads(json.dumps(defaults))
        )

    assert batched == single == expected
