import base64
import os
from unittest.mock import MagicMock, patch

import pytest

import open_webui.kms as kms_module
from open_webui.kms import _load_local, get_key, is_enabled, load_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_b64_key(length: int = 32) -> str:
    return base64.urlsafe_b64encode(os.urandom(length)).decode()


@pytest.fixture(autouse=True)
def reset_key():
    """Reset the module-level _key before every test."""
    kms_module._key = None
    yield
    kms_module._key = None


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all encryption-related env vars."""
    for var in ("BACKEND_ENCRYPTION", "CHAT_ENCRYPTION_KEY", "KMS_SECRET_NAME", "CHAT_ENCRYPTION_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    yield


# ---------------------------------------------------------------------------
# _load_local
# ---------------------------------------------------------------------------

class TestLoadLocal:
    def test_valid_key_returns_bytes(self, monkeypatch):
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(32))
        key = _load_local()
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("CHAT_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="CHAT_ENCRYPTION_KEY env var is required"):
            _load_local()

    def test_empty_string_raises(self, monkeypatch):
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", "")
        with pytest.raises(RuntimeError, match="CHAT_ENCRYPTION_KEY env var is required"):
            _load_local()

    def test_invalid_base64_raises(self, monkeypatch):
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", "not-valid-base64!!!")
        with pytest.raises(RuntimeError, match="not valid base64"):
            _load_local()

    def test_16_byte_key_raises(self, monkeypatch):
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(16))
        with pytest.raises(RuntimeError, match="32 bytes"):
            _load_local()

    def test_31_byte_key_raises(self, monkeypatch):
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(31))
        with pytest.raises(RuntimeError, match="32 bytes"):
            _load_local()

    def test_33_byte_key_raises(self, monkeypatch):
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(33))
        with pytest.raises(RuntimeError, match="32 bytes"):
            _load_local()

    def test_64_byte_key_raises(self, monkeypatch):
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(64))
        with pytest.raises(RuntimeError, match="32 bytes"):
            _load_local()

    def test_key_value_is_correct(self, monkeypatch):
        raw = os.urandom(32)
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", base64.urlsafe_b64encode(raw).decode())
        assert _load_local() == raw

    def test_key_with_padding_stripped(self, monkeypatch):
        # urlsafe_b64encode may include = padding — should still work
        raw = os.urandom(32)
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", encoded)
        assert _load_local() == raw

    def test_whitespace_in_key_raises(self, monkeypatch):
        key = _valid_b64_key(32) + "   "
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", key)
        with pytest.raises(RuntimeError):
            _load_local()


# ---------------------------------------------------------------------------
# load_key — local backend
# ---------------------------------------------------------------------------

class TestLoadKeyLocal:
    def test_loads_local_key_successfully(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "local")
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(32))
        load_key()
        assert kms_module._key is not None
        assert len(kms_module._key) == 32

    def test_default_backend_is_local(self, monkeypatch, clean_env):
        # No BACKEND_ENCRYPTION set — should default to local
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(32))
        load_key()
        assert kms_module._key is not None

    def test_unknown_backend_raises(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "hsm")
        with pytest.raises(RuntimeError, match="Unknown BACKEND_ENCRYPTION"):
            load_key()

    def test_backend_case_insensitive(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "LOCAL")
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(32))
        load_key()
        assert kms_module._key is not None

    def test_local_missing_key_raises(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "local")
        monkeypatch.delenv("CHAT_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="CHAT_ENCRYPTION_KEY"):
            load_key()


# ---------------------------------------------------------------------------
# load_key — gcp_kms backend (mocked)
# ---------------------------------------------------------------------------

class TestLoadKeyGcp:
    def _mock_secret_manager(self, raw_key: bytes):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.payload.data = base64.urlsafe_b64encode(raw_key)
        mock_client.access_secret_version.return_value = mock_response
        return mock_client

    def test_loads_gcp_key_successfully(self, monkeypatch, clean_env):
        raw = os.urandom(32)
        monkeypatch.setenv("BACKEND_ENCRYPTION", "gcp_kms")
        monkeypatch.setenv("KMS_SECRET_NAME", "projects/test/secrets/key/versions/latest")

        with patch("open_webui.kms._load_gcp", return_value=raw):
            load_key()

        assert kms_module._key == raw

    def test_missing_kms_secret_name_raises(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "gcp_kms")
        monkeypatch.delenv("KMS_SECRET_NAME", raising=False)
        with pytest.raises(RuntimeError, match="KMS_SECRET_NAME env var is required"):
            from open_webui.kms import _load_gcp
            _load_gcp()

    def test_empty_kms_secret_name_raises(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "gcp_kms")
        monkeypatch.setenv("KMS_SECRET_NAME", "")
        with pytest.raises(RuntimeError, match="KMS_SECRET_NAME env var is required"):
            from open_webui.kms import _load_gcp
            _load_gcp()

    def test_gcp_returns_invalid_base64_raises(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "gcp_kms")
        monkeypatch.setenv("KMS_SECRET_NAME", "projects/test/secrets/key/versions/latest")

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.payload.data = b"not-valid-base64!!!"
        mock_client.access_secret_version.return_value = mock_response

        mock_sm = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = mock_client

        with patch.dict("sys.modules", {"google.cloud.secretmanager": mock_sm, "google.cloud": MagicMock(secretmanager=mock_sm)}):
            with pytest.raises(RuntimeError, match="not valid base64"):
                from open_webui.kms import _load_gcp
                _load_gcp()

    def test_gcp_returns_wrong_size_key_raises(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "gcp_kms")
        monkeypatch.setenv("KMS_SECRET_NAME", "projects/test/secrets/key/versions/latest")

        wrong_size_raw = os.urandom(16)

        def fake_load_gcp():
            raise RuntimeError(f"Secret must decode to 32 bytes, got 16")

        with patch("open_webui.kms._load_gcp", fake_load_gcp):
            with pytest.raises(RuntimeError, match="32 bytes"):
                load_key()


# ---------------------------------------------------------------------------
# get_key
# ---------------------------------------------------------------------------

class TestGetKey:
    def test_raises_if_not_loaded(self, clean_env):
        with pytest.raises(RuntimeError, match="not loaded"):
            get_key()

    def test_returns_key_after_load(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "local")
        raw = os.urandom(32)
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", base64.urlsafe_b64encode(raw).decode())
        load_key()
        assert get_key() == raw

    def test_returns_same_key_on_multiple_calls(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "local")
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(32))
        load_key()
        assert get_key() is get_key()

    def test_key_is_32_bytes(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "local")
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(32))
        load_key()
        assert len(get_key()) == 32

    def test_key_is_bytes_type(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "local")
        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", _valid_b64_key(32))
        load_key()
        assert isinstance(get_key(), bytes)

    def test_second_load_overwrites_first(self, monkeypatch, clean_env):
        monkeypatch.setenv("BACKEND_ENCRYPTION", "local")
        raw1 = os.urandom(32)
        raw2 = os.urandom(32)

        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", base64.urlsafe_b64encode(raw1).decode())
        load_key()
        key1 = get_key()

        monkeypatch.setenv("CHAT_ENCRYPTION_KEY", base64.urlsafe_b64encode(raw2).decode())
        load_key()
        key2 = get_key()

        assert key1 != key2
        assert key2 == raw2


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------

class TestIsEnabled:
    def test_false_by_default(self, clean_env):
        assert is_enabled() is False

    def test_true_when_set(self, monkeypatch, clean_env):
        monkeypatch.setenv("CHAT_ENCRYPTION_ENABLED", "true")
        assert is_enabled() is True

    def test_false_when_explicitly_false(self, monkeypatch, clean_env):
        monkeypatch.setenv("CHAT_ENCRYPTION_ENABLED", "false")
        assert is_enabled() is False

    def test_case_insensitive_true(self, monkeypatch, clean_env):
        monkeypatch.setenv("CHAT_ENCRYPTION_ENABLED", "TRUE")
        assert is_enabled() is True

    def test_case_insensitive_mixed(self, monkeypatch, clean_env):
        monkeypatch.setenv("CHAT_ENCRYPTION_ENABLED", "True")
        assert is_enabled() is True

    def test_any_other_value_is_false(self, monkeypatch, clean_env):
        for val in ("1", "yes", "on", "enabled", ""):
            monkeypatch.setenv("CHAT_ENCRYPTION_ENABLED", val)
            assert is_enabled() is False
