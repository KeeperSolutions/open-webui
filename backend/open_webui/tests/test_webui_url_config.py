"""Tests for WEBUI_URL environment-variable-only enforcement."""

import asyncio
import os
from unittest.mock import MagicMock

import pytest

from open_webui.config import validate_webui_url


# ---------------------------------------------------------------------------
# Startup validation — tests against the real guard in config.py
# ---------------------------------------------------------------------------


class TestWebuiUrlStartupValidation:
    def test_raises_when_webui_url_missing(self):
        with pytest.raises(RuntimeError) as exc_info:
            validate_webui_url(os.environ.get("_MISSING_VAR_"))
        assert "WEBUI_URL" in str(exc_info.value)

    def test_raises_when_webui_url_empty_string(self):
        with pytest.raises(RuntimeError) as exc_info:
            validate_webui_url("")
        assert "WEBUI_URL" in str(exc_info.value)

    def test_raises_when_webui_url_invalid(self):
        for bad in ("lolo", "ftp://example.com", "example.com", "//example.com"):
            with pytest.raises(RuntimeError) as exc_info:
                validate_webui_url(bad)
            assert bad in str(exc_info.value)

    def test_error_message_is_actionable(self):
        with pytest.raises(RuntimeError) as exc_info:
            validate_webui_url("lolo")
        msg = str(exc_info.value)
        assert "WEBUI_URL" in msg
        assert "http://" in msg or "https://" in msg

    def test_does_not_raise_for_http(self):
        assert validate_webui_url("http://localhost:5173") == "http://localhost:5173"

    def test_does_not_raise_for_https(self):
        assert validate_webui_url("https://example.com") == "https://example.com"


# ---------------------------------------------------------------------------
# Admin config endpoint
# ---------------------------------------------------------------------------


def _make_request(webui_url="https://example.com"):
    request = MagicMock()
    request.app.state.WEBUI_URL = webui_url
    request.app.state.config.SHOW_ADMIN_DETAILS = True
    request.app.state.config.ADMIN_EMAIL = "admin@example.com"
    request.app.state.config.ENABLE_SIGNUP = True
    request.app.state.config.ENABLE_API_KEYS = True
    request.app.state.config.ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS = False
    request.app.state.config.API_KEYS_ALLOWED_ENDPOINTS = ""
    request.app.state.config.DEFAULT_USER_ROLE = "user"
    request.app.state.config.DEFAULT_GROUP_ID = ""
    request.app.state.config.JWT_EXPIRES_IN = "4w"
    request.app.state.config.ENABLE_COMMUNITY_SHARING = True
    request.app.state.config.ENABLE_MESSAGE_RATING = True
    request.app.state.config.ENABLE_FOLDERS = True
    request.app.state.config.FOLDER_MAX_FILE_COUNT = None
    request.app.state.config.ENABLE_CHANNELS = True
    request.app.state.config.ENABLE_MEMORIES = True
    request.app.state.config.ENABLE_NOTES = True
    request.app.state.config.ENABLE_USER_WEBHOOKS = False
    request.app.state.config.ENABLE_USER_STATUS = True
    request.app.state.config.PENDING_USER_OVERLAY_TITLE = None
    request.app.state.config.PENDING_USER_OVERLAY_CONTENT = None
    request.app.state.config.RESPONSE_WATERMARK = None
    return request


def _make_form_data(webui_url="https://ignored.example.com", **overrides):
    from open_webui.routers.auths import AdminConfig

    defaults = dict(
        SHOW_ADMIN_DETAILS=True,
        ADMIN_EMAIL="admin@example.com",
        WEBUI_URL=webui_url,
        ENABLE_SIGNUP=True,
        ENABLE_API_KEYS=True,
        ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS=False,
        API_KEYS_ALLOWED_ENDPOINTS="",
        DEFAULT_USER_ROLE="user",
        DEFAULT_GROUP_ID="",
        JWT_EXPIRES_IN="4w",
        ENABLE_COMMUNITY_SHARING=True,
        ENABLE_MESSAGE_RATING=True,
        ENABLE_FOLDERS=True,
        ENABLE_CHANNELS=True,
        ENABLE_MEMORIES=True,
        ENABLE_NOTES=True,
        ENABLE_USER_WEBHOOKS=False,
        ENABLE_USER_STATUS=True,
    )
    return AdminConfig(**{**defaults, **overrides})


class TestAdminConfigEndpoint:
    def test_get_admin_config_includes_env_controlled_flag(self):
        """GET /admin/config must always include WEBUI_URL_ENV_CONTROLLED: True."""
        from open_webui.routers.auths import get_admin_config

        request = _make_request()
        result = asyncio.run(get_admin_config(request=request, user=MagicMock()))

        assert result["WEBUI_URL_ENV_CONTROLLED"] is True

    def test_get_admin_config_returns_webui_url_from_config(self):
        """GET /admin/config must return the current WEBUI_URL value."""
        from open_webui.routers.auths import get_admin_config

        request = _make_request(webui_url="https://my-instance.example.com")
        result = asyncio.run(get_admin_config(request=request, user=MagicMock()))

        assert result["WEBUI_URL"] == "https://my-instance.example.com"

    def test_post_admin_config_does_not_overwrite_webui_url(self):
        """POST /admin/config must not update WEBUI_URL on app.state.config."""
        from open_webui.routers.auths import update_admin_config

        original_url = "https://original.example.com"
        request = _make_request(webui_url=original_url)
        form_data = _make_form_data(webui_url="https://attacker-supplied.example.com")

        asyncio.run(
            update_admin_config(request=request, form_data=form_data, user=MagicMock())
        )

        assert request.app.state.WEBUI_URL == original_url

    def test_post_admin_config_still_updates_other_fields(self):
        """POST /admin/config must still update unrelated fields normally."""
        from open_webui.routers.auths import update_admin_config

        request = _make_request()
        form_data = _make_form_data(
            SHOW_ADMIN_DETAILS=False,
            ENABLE_SIGNUP=False,
            DEFAULT_USER_ROLE="pending",
        )

        asyncio.run(
            update_admin_config(request=request, form_data=form_data, user=MagicMock())
        )

        assert request.app.state.config.SHOW_ADMIN_DETAILS is False
        assert request.app.state.config.ENABLE_SIGNUP is False
        assert request.app.state.config.DEFAULT_USER_ROLE == "pending"
