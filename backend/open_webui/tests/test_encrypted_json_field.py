"""Integration tests for EncryptedJSONField TypeDecorator.

Tests cover:
- Transparent encrypt-on-write / decrypt-on-read when encryption is enabled
- Plaintext fallback: legacy rows (no ENC1: prefix) are returned as-is
- Pass-through behaviour when encryption is disabled
"""

import json
import os
from unittest.mock import patch

import pytest

from open_webui.internal.db import EncryptedJSONField
from open_webui.crypto import decrypt
from sqlalchemy import Dialect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeDialect(Dialect):
    """Minimal dialect stub — EncryptedJSONField doesn't use dialect-specific features."""
    pass


_DIALECT = _FakeDialect()

SAMPLE_PAYLOAD = {
    "title": "Test Chat",
    "history": {
        "messages": {
            "msg-1": {"role": "user", "content": "hello"},
            "msg-2": {"role": "assistant", "content": "hi there"},
        }
    },
}


@pytest.fixture
def key_32():
    return os.urandom(32)


@pytest.fixture
def field():
    return EncryptedJSONField()


# ---------------------------------------------------------------------------
# Encryption enabled — write path
# ---------------------------------------------------------------------------

class TestEncryptedWritePath:
    def test_stored_value_starts_with_enc1_prefix(self, field, key_32):
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)
        assert stored.startswith("ENC1:")

    def test_stored_value_is_not_plain_json(self, field, key_32):
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)
        # Must not be decodable as-is JSON
        with pytest.raises(Exception):
            json.loads(stored)

    def test_two_writes_same_payload_produce_different_ciphertext(self, field, key_32):
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            s1 = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)
            s2 = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)
        assert s1 != s2  # different nonce each time


# ---------------------------------------------------------------------------
# Encryption enabled — read path (round-trip)
# ---------------------------------------------------------------------------

class TestEncryptedReadPath:
    def test_round_trip_restores_original_payload(self, field, key_32):
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)
            result = field.process_result_value(stored, _DIALECT)
        assert result == SAMPLE_PAYLOAD

    def test_round_trip_preserves_nested_structure(self, field, key_32):
        payload = {"a": {"b": {"c": [1, 2, 3]}}}
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param(payload, _DIALECT)
            result = field.process_result_value(stored, _DIALECT)
        assert result == payload

    def test_round_trip_none_value(self, field, key_32):
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param(None, _DIALECT)
            result = field.process_result_value(stored, _DIALECT)
        assert result is None

    def test_round_trip_empty_dict(self, field, key_32):
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param({}, _DIALECT)
            result = field.process_result_value(stored, _DIALECT)
        assert result == {}

    def test_round_trip_unicode_content(self, field, key_32):
        payload = {"message": "こんにちは 🔐"}
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param(payload, _DIALECT)
            result = field.process_result_value(stored, _DIALECT)
        assert result == payload


# ---------------------------------------------------------------------------
# Plaintext fallback — legacy rows without ENC1: prefix
# ---------------------------------------------------------------------------

class TestPlaintextFallback:
    def test_legacy_plaintext_row_returned_as_dict(self, field):
        legacy = json.dumps(SAMPLE_PAYLOAD)
        result = field.process_result_value(legacy, _DIALECT)
        assert result == SAMPLE_PAYLOAD

    def test_legacy_row_does_not_require_kms(self, field):
        """Reading a legacy row must not call kms.get_key() — key may not be loaded."""
        legacy = json.dumps({"title": "old chat"})
        with patch("open_webui.kms.get_key", side_effect=RuntimeError("should not be called")):
            result = field.process_result_value(legacy, _DIALECT)
        assert result == {"title": "old chat"}

    def test_none_returns_none(self, field):
        assert field.process_result_value(None, _DIALECT) is None

    def test_already_deserialized_dict_returned_as_is(self, field):
        """PostgreSQL native JSON columns may hand SQLAlchemy a dict directly."""
        result = field.process_result_value(SAMPLE_PAYLOAD, _DIALECT)
        assert result is SAMPLE_PAYLOAD

    def test_already_deserialized_list_returned_as_is(self, field):
        payload = [1, 2, {"a": "b"}]
        result = field.process_result_value(payload, _DIALECT)
        assert result is payload


# ---------------------------------------------------------------------------
# Encryption disabled — pass-through
# ---------------------------------------------------------------------------

class TestEncryptionDisabled:
    def test_stored_value_is_plain_json_when_disabled(self, field):
        with patch("open_webui.kms.is_enabled", return_value=False):
            stored = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)
        assert not stored.startswith("ENC1:")
        assert json.loads(stored) == SAMPLE_PAYLOAD

    def test_read_back_plain_json_when_disabled(self, field):
        with patch("open_webui.kms.is_enabled", return_value=False):
            stored = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)
            result = field.process_result_value(stored, _DIALECT)
        assert result == SAMPLE_PAYLOAD

    def test_wrong_key_raises_on_read(self, field, key_32):
        """Row encrypted with key A must fail if read with key B."""
        key_b = os.urandom(32)
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)

        with patch("open_webui.kms.get_key", return_value=key_b):
            with pytest.raises(Exception):
                field.process_result_value(stored, _DIALECT)


# ---------------------------------------------------------------------------
# Corrupted / truncated tokens
# ---------------------------------------------------------------------------

class TestEncryptionDisabledWithEncryptedRows:
    def test_encrypted_row_with_encryption_disabled_raises_clear_error(self, field, key_32):
        """Reading an ENC1: row when CHAT_ENCRYPTION_ENABLED=false must fail with a helpful message."""
        with patch("open_webui.kms.is_enabled", return_value=True), \
             patch("open_webui.kms.get_key", return_value=key_32):
            stored = field.process_bind_param(SAMPLE_PAYLOAD, _DIALECT)

        with patch("open_webui.kms.is_enabled", return_value=False):
            with pytest.raises(RuntimeError, match="CHAT_ENCRYPTION_ENABLED"):
                field.process_result_value(stored, _DIALECT)


class TestCorruptedTokens:
    def test_empty_payload_raises_value_error(self, key_32):
        with pytest.raises(ValueError, match="too short"):
            decrypt("ENC1:", key_32)

    def test_prefix_only_no_base64_raises(self, key_32):
        with pytest.raises(ValueError):
            decrypt("ENC1:!!!not-base64!!!", key_32)

    def test_truncated_blob_raises_value_error(self, key_32):
        # Valid base64 but only a few bytes — shorter than minimum blob size
        import base64
        short = base64.b64encode(b"\x01" * 5).decode()
        with pytest.raises(ValueError, match="too short"):
            decrypt(f"ENC1:{short}", key_32)
