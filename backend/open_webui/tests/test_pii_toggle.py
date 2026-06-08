"""
Tests for the per-request PII masking override in process_pipeline_inlet_filter.

The function reads pii_masking_enabled from user.settings["ui"]["pipelines"]["valves"][filter_id]
and passes it as part of the user dict to the external pipeline service. When the request
payload contains features.pii_masking (bool), that value overrides the stored setting for
this request only — no DB write occurs.

We mock aiohttp.ClientSession to capture the request_data dict passed to session.post()
and assert the valves field that the pipeline would receive.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# stripe is an optional billing dependency not installed in the test environment.
# Mock it before any open_webui import triggers the import chain.
sys.modules.setdefault("stripe", MagicMock())

from open_webui.routers.pipelines import process_pipeline_inlet_filter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(pii_enabled=None, filter_id="pii_filter"):
    """Build a minimal user object with optional stored pii_masking_enabled."""
    settings_dict = {}
    if pii_enabled is not None:
        settings_dict = {
            "ui": {
                "pipelines": {
                    "valves": {
                        filter_id: {"pii_masking_enabled": pii_enabled}
                    }
                }
            }
        }

    class _Settings:
        def model_dump(self):
            return settings_dict

    user = SimpleNamespace(
        id="user-1",
        email="test@example.com",
        name="Test User",
        role="user",
        settings=_Settings() if pii_enabled is not None else None,
    )
    return user


def _make_request(base_urls=None, api_keys=None):
    """Build a minimal FastAPI request stub."""
    request = MagicMock()
    request.app.state.config.OPENAI_API_BASE_URLS = base_urls or ["http://pipeline-host"]
    request.app.state.config.OPENAI_API_KEYS = api_keys or ["secret-key"]
    return request


def _make_models(filter_id="pii_filter", url_idx=0):
    """Return a models dict containing one pipeline filter."""
    return {
        "gpt-4": {"id": "gpt-4"},
        filter_id: {
            "id": filter_id,
            "urlIdx": url_idx,
            "pipeline": {
                "type": "filter",
                "priority": 0,
                "pipelines": ["*"],
            },
        },
    }


def _run(coro):
    return asyncio.run(coro)


def _patch_session(captured: list):
    """
    Patch aiohttp.ClientSession so that `async with session.post(...) as resp`
    captures request_data and returns a successful response with the body unchanged.

    The production code does:
        async with aiohttp.ClientSession(...) as session:
            async with session.post(url, ..., json=request_data, ...) as response:
                payload = await response.json()
                response.raise_for_status()

    So we need:
    - ClientSession() to be an async context manager yielding a session object
    - session.post(...) to be an async context manager yielding a response object
    """
    def _make_response_cm(request_data):
        resp = MagicMock()
        resp.json = AsyncMock(return_value=request_data["body"])
        resp.raise_for_status = MagicMock()
        resp.content_type = "application/json"
        resp_cm = MagicMock()
        resp_cm.__aenter__ = AsyncMock(return_value=resp)
        resp_cm.__aexit__ = AsyncMock(return_value=False)
        return resp_cm

    def _fake_post(url, *, headers, json, ssl):
        captured.append(json)
        return _make_response_cm(json)

    session = MagicMock()
    session.post = _fake_post

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    return patch("open_webui.routers.pipelines.aiohttp.ClientSession", return_value=session_cm)


# ---------------------------------------------------------------------------
# Valve resolution — existing logic (no per-request features key)
# ---------------------------------------------------------------------------

class TestStoredSettingResolution:

    def test_stored_true_reaches_pipeline(self):
        captured = []
        payload = {"model": "gpt-4"}
        user = _make_user(pii_enabled=True)

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        assert len(captured) == 1
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is True

    def test_stored_false_reaches_pipeline(self):
        captured = []
        payload = {"model": "gpt-4"}
        user = _make_user(pii_enabled=False)

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        assert len(captured) == 1
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is False

    def test_no_stored_setting_sends_empty_valves(self):
        captured = []
        payload = {"model": "gpt-4"}
        user = _make_user()  # settings=None

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        assert len(captured) == 1
        assert captured[0]["user"]["valves"] == {}


# ---------------------------------------------------------------------------
# Per-request override — new logic
# ---------------------------------------------------------------------------

class TestPerRequestOverride:

    def test_true_overrides_stored_false(self):
        captured = []
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}
        user = _make_user(pii_enabled=False)

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is True

    def test_false_overrides_stored_true(self):
        captured = []
        payload = {"model": "gpt-4", "features": {"pii_masking": False}}
        user = _make_user(pii_enabled=True)

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is False

    def test_false_populates_empty_valves(self):
        captured = []
        payload = {"model": "gpt-4", "features": {"pii_masking": False}}
        user = _make_user()  # no stored setting

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is False

    def test_none_feature_value_does_not_override(self):
        captured = []
        payload = {"model": "gpt-4", "features": {"pii_masking": None}}
        user = _make_user(pii_enabled=False)

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        # None is not bool — stored value must be preserved
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is False

    def test_string_feature_value_does_not_override(self):
        captured = []
        payload = {"model": "gpt-4", "features": {"pii_masking": "true"}}
        user = _make_user(pii_enabled=False)

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        # "true" string is not bool — stored value must be preserved
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is False

    def test_null_features_does_not_raise(self):
        captured = []
        payload = {"model": "gpt-4", "features": None}
        user = _make_user(pii_enabled=True)

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        # features=null must not raise; stored value is preserved
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is True

    def test_non_dict_features_does_not_raise(self):
        captured = []
        payload = {"model": "gpt-4", "features": ["pii_masking"]}
        user = _make_user(pii_enabled=True)

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(
                _make_request(), payload, user, _make_models()
            ))

        # features as a list must not raise; stored value is preserved
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is True


# ---------------------------------------------------------------------------
# Multiple filters — override applies independently to each
# ---------------------------------------------------------------------------

class TestMultipleFilters:

    def test_override_applies_to_both_filters_independently(self):
        """Two filters each get their own stored valves; override applies to both."""
        captured = []
        payload = {"model": "gpt-4", "features": {"pii_masking": True}}

        # Each filter has a different stored value
        models = {
            "gpt-4": {"id": "gpt-4"},
            "pii_filter": {
                "id": "pii_filter",
                "urlIdx": 0,
                "pipeline": {"type": "filter", "priority": 0, "pipelines": ["*"]},
            },
            "pii_filter_pipeline": {
                "id": "pii_filter_pipeline",
                "urlIdx": 0,
                "pipeline": {"type": "filter", "priority": 1, "pipelines": ["*"]},
            },
        }
        user_settings = {
            "ui": {
                "pipelines": {
                    "valves": {
                        "pii_filter": {"pii_masking_enabled": False},
                        "pii_filter_pipeline": {"pii_masking_enabled": False},
                    }
                }
            }
        }

        class _Settings:
            def model_dump(self):
                return user_settings

        user = SimpleNamespace(
            id="user-1",
            email="test@example.com",
            name="Test User",
            role="user",
            settings=_Settings(),
        )

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(_make_request(), payload, user, models))

        assert len(captured) == 2
        for call in captured:
            assert call["user"]["valves"]["pii_masking_enabled"] is True

    def test_no_cross_contamination_between_filters(self):
        """Each filter only sees its own stored valves; no bleed between them."""
        captured = []
        payload = {"model": "gpt-4"}  # no features override

        models = {
            "gpt-4": {"id": "gpt-4"},
            "pii_filter": {
                "id": "pii_filter",
                "urlIdx": 0,
                "pipeline": {"type": "filter", "priority": 0, "pipelines": ["*"]},
            },
            "pii_filter_pipeline": {
                "id": "pii_filter_pipeline",
                "urlIdx": 0,
                "pipeline": {"type": "filter", "priority": 1, "pipelines": ["*"]},
            },
        }
        user_settings = {
            "ui": {
                "pipelines": {
                    "valves": {
                        "pii_filter": {"pii_masking_enabled": True},
                        "pii_filter_pipeline": {"pii_masking_enabled": False},
                    }
                }
            }
        }

        class _Settings:
            def model_dump(self):
                return user_settings

        user = SimpleNamespace(
            id="user-1",
            email="test@example.com",
            name="Test User",
            role="user",
            settings=_Settings(),
        )

        with _patch_session(captured):
            _run(process_pipeline_inlet_filter(_make_request(), payload, user, models))

        assert len(captured) == 2
        # First filter (priority 0) → pii_filter → True
        assert captured[0]["user"]["valves"]["pii_masking_enabled"] is True
        # Second filter (priority 1) → pii_filter_pipeline → False
        assert captured[1]["user"]["valves"]["pii_masking_enabled"] is False
