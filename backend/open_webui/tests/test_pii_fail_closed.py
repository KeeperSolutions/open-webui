"""
Fail-closed PII masking tests.

Security-critical: when PII masking is requested for a chat but the masking
pipeline cannot be applied, the request MUST be refused — never sent to the LLM
unmasked. Two independent mechanisms are covered:

  * Mechanism 1 (process_pipeline_inlet_filter): the pipeline is reachable in the
    registry but the inlet HTTP call fails (connection error / timeout). For a
    required PII filter with masking ON this now re-raises instead of returning
    the original unmasked payload. Non-PII filters (e.g. telemetry) stay
    best-effort. A 5xx {"detail": ...} (block mode) stays fail-closed as before.

  * Mechanism 2 (assert_pii_masking_available): the PII filter was pruned from
    app.state.MODELS because its /models fetch failed while the pipeline was down,
    so it is absent from the resolved filters and the inlet would be skipped
    entirely. The guard refuses the request before any LLM call.

The masking-OFF path must NEVER be blocked by pipeline unavailability.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

# stripe is an optional billing dependency not installed in the test environment.
sys.modules.setdefault("stripe", MagicMock())

from open_webui.routers.pipelines import (
    process_pipeline_inlet_filter,
    assert_pii_masking_available,
    PiiMaskingUnavailableError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_user():
    return SimpleNamespace(
        id="user-1", email="test@example.com", name="Test User", role="user",
        settings=None,
    )


def _make_request():
    request = MagicMock()
    request.app.state.config.OPENAI_API_BASE_URLS = ["http://pipeline-host"]
    request.app.state.config.OPENAI_API_KEYS = ["secret-key"]
    return request


def _make_models(filter_id="pii_filter", url_idx=0):
    return {
        "gpt-4": {"id": "gpt-4"},
        filter_id: {
            "id": filter_id,
            "urlIdx": url_idx,
            "pipeline": {"type": "filter", "priority": 0, "pipelines": ["*"]},
        },
    }


def _patch_session_success(captured):
    """Successful inlet: session.post returns the body unchanged (masked)."""
    def _make_cm(request_data):
        resp = MagicMock()
        resp.json = AsyncMock(return_value=request_data["body"])
        resp.raise_for_status = MagicMock()
        resp.content_type = "application/json"
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def _fake_post(url, *, headers, json, ssl):
        captured.append(json)
        return _make_cm(json)

    session = MagicMock()
    session.post = _fake_post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "open_webui.routers.pipelines.aiohttp.ClientSession",
        return_value=session_cm,
    )


def _patch_session_failing(exc=None):
    """Connection failure: entering `async with session.post(...)` raises
    (e.g. timeout / connection refused) — no HTTP response is ever received."""
    exc = exc if exc is not None else asyncio.TimeoutError()

    def _fake_post(url, *, headers, json, ssl):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=exc)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    session = MagicMock()
    session.post = _fake_post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "open_webui.routers.pipelines.aiohttp.ClientSession",
        return_value=session_cm,
    )


def _make_models_pii_and_langfuse():
    """A PII filter (priority 0) plus a non-PII telemetry filter (priority 1)."""
    return {
        "gpt-4": {"id": "gpt-4"},
        "pii_filter": {
            "id": "pii_filter",
            "urlIdx": 0,
            "pipeline": {"type": "filter", "priority": 0, "pipelines": ["*"]},
        },
        "langfuse_v4_filter_pipeline": {
            "id": "langfuse_v4_filter_pipeline",
            "urlIdx": 0,
            "pipeline": {"type": "filter", "priority": 1, "pipelines": ["*"]},
        },
    }


def _patch_session_selective(fail_ids):
    """Per-filter behaviour: filters whose id is in `fail_ids` raise a connection
    error; every other filter succeeds (echoes the body)."""
    def _fake_post(url, *, headers, json, ssl):
        if any(f"/{fid}/filter/" in url for fid in fail_ids):
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm
        resp = MagicMock()
        resp.json = AsyncMock(return_value=json["body"])
        resp.raise_for_status = MagicMock()
        resp.content_type = "application/json"
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    session = MagicMock()
    session.post = _fake_post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "open_webui.routers.pipelines.aiohttp.ClientSession",
        return_value=session_cm,
    )


def _patch_session_5xx_detail():
    """Block mode: HTTP 5xx with a JSON {"detail": ...} body (raise_for_status
    raises ClientResponseError). This path was already fail-closed."""
    def _fake_post(url, *, headers, json, ssl):
        resp = MagicMock()
        resp.json = AsyncMock(return_value={"detail": "PII filter blocked the request"})
        resp.content_type = "application/json"
        resp.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(MagicMock(), (), status=500)
        )
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    session = MagicMock()
    session.post = _fake_post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "open_webui.routers.pipelines.aiohttp.ClientSession",
        return_value=session_cm,
    )


# ---------------------------------------------------------------------------
# Mechanism 1 — inlet connection failure
# ---------------------------------------------------------------------------

class TestInletFailClosed:

    def test_masking_on_connection_error_raises(self):
        # masking ON + PII filter unreachable → REFUSE (payload never returned).
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        with _patch_session_failing():
            with pytest.raises(PiiMaskingUnavailableError):
                _run(process_pipeline_inlet_filter(
                    _make_request(), payload, _make_user(), _make_models()
                ))

    def test_masking_off_connection_error_passes(self):
        # masking OFF + pipeline unavailable → MUST pass (nothing to mask).
        payload = {"model": "gpt-4", "features": {"pii_masking": False}}
        with _patch_session_failing():
            result = _run(process_pipeline_inlet_filter(
                _make_request(), payload, _make_user(), _make_models()
            ))
        assert result == payload  # swallowed, best-effort passthrough, no raise

    def test_masking_on_pipeline_ok_masks_no_raise(self):
        # masking ON + pipeline OK → normal masking, no raise (regression).
        captured = []
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        with _patch_session_success(captured):
            result = _run(process_pipeline_inlet_filter(
                _make_request(), payload, _make_user(), _make_models()
            ))
        assert result["model"] == "gpt-4"
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is True

    def test_masking_off_pipeline_ok_passes_unmasked(self):
        # masking OFF + pipeline OK → passes through unmasked (regression).
        captured = []
        payload = {"model": "gpt-4", "features": {"pii_masking": False}}
        with _patch_session_success(captured):
            result = _run(process_pipeline_inlet_filter(
                _make_request(), payload, _make_user(), _make_models()
            ))
        assert result["model"] == "gpt-4"
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is False

    def test_non_pii_filter_failure_does_not_block_when_pii_ok(self):
        # A NON-required filter (Langfuse) failing must NOT block chat as long as
        # the PII filter itself applied. Only PII filters are mandatory.
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        models = _make_models_pii_and_langfuse()  # PII present → guard passes
        with _patch_session_selective({"langfuse_v4_filter_pipeline"}):
            result = _run(process_pipeline_inlet_filter(
                _make_request(), payload, _make_user(), models
            ))
        # pii_filter masked OK; langfuse connection error swallowed; no raise.
        assert result["model"] == "gpt-4"

    def test_5xx_with_detail_still_fails_closed(self):
        # Block-mode 5xx {"detail": ...} stays fail-closed as before (regression).
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        with _patch_session_5xx_detail():
            with pytest.raises(Exception):
                _run(process_pipeline_inlet_filter(
                    _make_request(), payload, _make_user(), _make_models()
                ))


# ---------------------------------------------------------------------------
# Mechanism 2 — required-but-absent filter guard (registry pruning)
# ---------------------------------------------------------------------------

class TestAssertPiiMaskingAvailable:

    def test_masking_on_filter_present_no_raise(self):
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        assert_pii_masking_available(payload, "gpt-4", _make_models())  # no raise

    def test_masking_on_filter_absent_raises(self):
        # Pipeline down → filter pruned from registry → REFUSE.
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        models = {"gpt-4": {"id": "gpt-4"}}  # PII filter absent
        with pytest.raises(PiiMaskingUnavailableError):
            assert_pii_masking_available(payload, "gpt-4", models)

    def test_masking_off_filter_absent_no_raise(self):
        # masking OFF → pipeline unavailability must NOT block.
        payload = {"model": "gpt-4", "features": {"pii_masking": False}}
        models = {"gpt-4": {"id": "gpt-4"}}
        assert_pii_masking_available(payload, "gpt-4", models)  # no raise

    def test_masking_unspecified_filter_absent_no_raise(self):
        # No features at all → masking not requested → do not block.
        payload = {"model": "gpt-4"}
        models = {"gpt-4": {"id": "gpt-4"}}
        assert_pii_masking_available(payload, "gpt-4", models)  # no raise

    def test_metadata_features_true_filter_absent_raises(self):
        # Task-shaped payload (metadata.features) is enforced too.
        payload = {"model": "gpt-4", "metadata": {"features": {"pii_masking": True}}}
        models = {"gpt-4": {"id": "gpt-4"}}
        with pytest.raises(PiiMaskingUnavailableError):
            assert_pii_masking_available(payload, "gpt-4", models)

    def test_enforcement_disabled_no_raise(self):
        # PII_FILTER_IDS empty → fail-closed enforcement off entirely.
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        models = {"gpt-4": {"id": "gpt-4"}}
        with patch("open_webui.routers.pipelines.PII_FILTER_IDS", set()):
            assert_pii_masking_available(payload, "gpt-4", models)  # no raise

    def test_error_message_is_user_facing(self):
        # str(e) is shown to the user verbatim — must be clear, non-technical.
        err = PiiMaskingUnavailableError()
        assert "masking" in str(err).lower()
        assert "not sent" in str(err).lower()


# ---------------------------------------------------------------------------
# The guard lives INSIDE process_pipeline_inlet_filter — the single chokepoint
# every inlet caller flows through (main chat AND all 8 task-generator sites).
# ---------------------------------------------------------------------------

class TestInletGuardCoversAllCallers:

    def test_masking_on_filter_pruned_raises(self):
        # Pruned registry (no PII filter) + masking ON → refuse. The guard fires
        # before any HTTP call, so this holds for EVERY caller, incl. tasks.
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        models = {"gpt-4": {"id": "gpt-4"}}  # PII filter pruned
        with pytest.raises(PiiMaskingUnavailableError):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, _make_user(), models
            ))

    def test_masking_off_filter_pruned_passes(self):
        # masking OFF + pruned → MUST pass (nothing to mask).
        payload = {"model": "gpt-4", "features": {"pii_masking": False}}
        models = {"gpt-4": {"id": "gpt-4"}}
        result = _run(process_pipeline_inlet_filter(
            _make_request(), payload, _make_user(), models
        ))
        assert result == payload

    def test_task_shaped_metadata_features_pruned_raises(self):
        # Task payloads carry the toggle under metadata.features (tasks.py:364),
        # with no top-level features. The guard reads it via the same resolver.
        payload = {
            "model": "gpt-4",
            "metadata": {"task": "title_generation", "features": {"pii_masking": True}},
        }
        models = {"gpt-4": {"id": "gpt-4"}}  # pruned
        with pytest.raises(PiiMaskingUnavailableError):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, _make_user(), models
            ))

    def test_task_shaped_metadata_off_pruned_passes(self):
        payload = {
            "model": "gpt-4",
            "metadata": {"task": "title_generation", "features": {"pii_masking": False}},
        }
        models = {"gpt-4": {"id": "gpt-4"}}
        result = _run(process_pipeline_inlet_filter(
            _make_request(), payload, _make_user(), models
        ))
        assert result == payload


# ---------------------------------------------------------------------------
# End-to-end task generator: a pruned registry must refuse WITHOUT calling the
# LLM, so chat history (with PII) never leaks via title/tags/follow-ups.
# ---------------------------------------------------------------------------

class TestTaskGeneratorFailClosed:

    def test_generate_title_pruned_registry_refuses_no_llm_call(self):
        from open_webui.routers.tasks import generate_title

        request = MagicMock()
        request.app.state.config.ENABLE_TITLE_GENERATION = True
        request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE = ""
        request.app.state.config.TASK_MODEL = None
        request.app.state.config.TASK_MODEL_EXTERNAL = None
        request.app.state.config.OPENAI_API_BASE_URLS = ["http://pipeline-host"]
        request.app.state.config.OPENAI_API_KEYS = ["secret-key"]
        request.app.state.MODELS = {"gpt-4": {"id": "gpt-4"}}  # PII filter pruned
        # masking ON, carried the task way (metadata.features via request.state).
        request.state = SimpleNamespace(metadata={"features": {"pii_masking": True}})

        form_data = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "My name is Marcus Thornbury"}],
            "chat_id": "chat-1",
        }

        llm_spy = AsyncMock()
        with patch("open_webui.routers.tasks.get_task_model_id", MagicMock(return_value="gpt-4")), \
             patch("open_webui.routers.tasks.title_generation_template", MagicMock(return_value="prompt")), \
             patch("open_webui.routers.tasks.generate_chat_completion", llm_spy):
            with pytest.raises(PiiMaskingUnavailableError):
                _run(generate_title(request, form_data, _make_user()))

        # The refusal happened at the inlet → the LLM was never called.
        assert llm_spy.await_count == 0
