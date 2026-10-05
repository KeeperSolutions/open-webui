"""With no per-request flag, the pipeline receives the user's choice or the instance default.

The pipeline's own built-in default is ON, so a missing key would ignore an
instance default of OFF.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules.setdefault("stripe", MagicMock())

from open_webui.routers.pipelines import process_pipeline_inlet_filter
from open_webui.tests.test_pii_toggle import (
    _make_models,
    _make_request,
    _make_user,
    _patch_session,
    _run,
)


def _inlet(user, default, payload=None):
    request = _make_request()
    request.app.state.config.PII_MASKING_DEFAULT_ENABLED = default
    captured = []
    with patch(
        "open_webui.routers.pipelines.resolve_pii_masking_enforced",
        AsyncMock(return_value=False),
    ), _patch_session(captured):
        _run(
            process_pipeline_inlet_filter(
                request, payload or {"model": "gpt-4"}, user, _make_models()
            )
        )
    return captured[0]["user"]["valves"]


def test_unset_user_gets_the_instance_default():
    assert _inlet(_make_user(), False)["pii_masking_enabled"] is False
    assert _inlet(_make_user(), True)["pii_masking_enabled"] is True


def test_stored_choice_beats_the_instance_default():
    assert _inlet(_make_user(pii_enabled=True), False)["pii_masking_enabled"] is True


def test_request_flag_beats_both():
    payload = {"model": "gpt-4", "features": {"pii_masking": True}}
    assert _inlet(_make_user(pii_enabled=False), False, payload)["pii_masking_enabled"] is True


def test_source_masking_decision_uses_the_instance_default():
    from open_webui.utils.middleware import _resolve_pii_masking_decision

    request = MagicMock()
    request.app.state.config.PII_MASKING_DEFAULT_ENABLED = False
    with patch(
        "open_webui.utils.middleware.resolve_pii_masking_enforced",
        AsyncMock(return_value=False),
    ):
        assert _run(_resolve_pii_masking_decision(request, _make_user(), {})) == (False, False)
        assert _run(
            _resolve_pii_masking_decision(request, _make_user(pii_enabled=True), {})
        ) == (False, True)


def test_ingest_scan_uses_the_instance_default():
    from open_webui.routers import retrieval

    request = MagicMock()
    request.app.state.config.PII_MASKING_DEFAULT_ENABLED = False
    assert retrieval._user_pii_masking_enabled(request, _make_user()) is False


def test_only_false_turns_the_env_default_off():
    """Any value other than "false" keeps masking on, so a typo cannot disable it."""
    from open_webui.config import parse_pii_masking_default

    for value in ("True", "true", "1", "yes", "on", " True ", ""):
        assert parse_pii_masking_default(value) is True
    for value in ("false", "FALSE", " False "):
        assert parse_pii_masking_default(value) is False


def _inlet_enforced(user, default, payload=None):
    request = _make_request()
    request.app.state.config.PII_MASKING_DEFAULT_ENABLED = default
    captured = []
    with patch(
        "open_webui.routers.pipelines.resolve_pii_masking_enforced",
        AsyncMock(return_value=True),
    ), _patch_session(captured):
        _run(
            process_pipeline_inlet_filter(
                request, payload or {"model": "gpt-4"}, user, _make_models()
            )
        )
    return captured[0]["user"]["valves"]


def test_enforcing_policy_beats_an_instance_default_of_off():
    """A mandated policy masks even when the instance default is off and the chat toggle is off."""
    assert _inlet_enforced(_make_user(), False)["pii_masking_enabled"] is True
    payload = {"model": "gpt-4", "features": {"pii_masking": False}}
    assert _inlet_enforced(_make_user(), False, payload)["pii_masking_enabled"] is True


def test_source_masking_decision_policy_beats_an_instance_default_of_off():
    from open_webui.utils.middleware import _resolve_pii_masking_decision

    request = MagicMock()
    request.app.state.config.PII_MASKING_DEFAULT_ENABLED = False
    with patch(
        "open_webui.utils.middleware.resolve_pii_masking_enforced",
        AsyncMock(return_value=True),
    ):
        assert _run(_resolve_pii_masking_decision(request, _make_user(), {})) == (True, True)
        assert _run(
            _resolve_pii_masking_decision(request, _make_user(), {"pii_masking": False})
        ) == (True, True)
