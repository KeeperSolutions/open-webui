import base64
import os
import struct

import pytest

from open_webui.crypto import (
    PREFIX,
    VERSION,
    KEY_ID_SIZE,
    NONCE_SIZE,
    _decode_key_id,
    _encode_key_id,
    decrypt,
    encrypt,
    is_encrypted,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def key():
    return os.urandom(32)


@pytest.fixture
def key2():
    return os.urandom(32)


@pytest.fixture
def plaintext():
    return b'{"title": "test chat", "history": {"messages": {}}}'


# ---------------------------------------------------------------------------
# _encode_key_id / _decode_key_id
# ---------------------------------------------------------------------------

class TestKeyIdEncoding:
    def test_roundtrip_version_1(self):
        assert _decode_key_id(_encode_key_id("1")) == "1"

    def test_roundtrip_large_version(self):
        assert _decode_key_id(_encode_key_id("999")) == "999"

    def test_encode_produces_4_bytes(self):
        assert len(_encode_key_id("1")) == KEY_ID_SIZE

    def test_encode_is_big_endian(self):
        result = _encode_key_id("1")
        assert result == struct.pack(">I", 1)

    def test_zero_key_id(self):
        assert _decode_key_id(_encode_key_id("0")) == "0"

    def test_max_key_id(self):
        max_val = str(2**32 - 1)
        assert _decode_key_id(_encode_key_id(max_val)) == max_val


# ---------------------------------------------------------------------------
# is_encrypted
# ---------------------------------------------------------------------------

class TestIsEncrypted:
    def test_true_for_enc1_prefix(self, key, plaintext):
        token = encrypt(plaintext, key)
        assert is_encrypted(token) is True

    def test_false_for_plain_json(self):
        assert is_encrypted('{"title": "hello"}') is False

    def test_false_for_empty_string(self):
        assert is_encrypted("") is False

    def test_false_for_none(self):
        assert is_encrypted(None) is False  # type: ignore

    def test_false_for_integer(self):
        assert is_encrypted(42) is False  # type: ignore

    def test_false_for_partial_prefix(self):
        assert is_encrypted("ENC") is False

    def test_false_for_wrong_version_prefix(self):
        assert is_encrypted("ENC2:abc") is False


# ---------------------------------------------------------------------------
# encrypt
# ---------------------------------------------------------------------------

class TestEncrypt:
    def test_returns_string(self, key, plaintext):
        assert isinstance(encrypt(plaintext, key), str)

    def test_starts_with_prefix(self, key, plaintext):
        assert encrypt(plaintext, key).startswith(PREFIX)

    def test_different_nonce_each_call(self, key, plaintext):
        t1 = encrypt(plaintext, key)
        t2 = encrypt(plaintext, key)
        assert t1 != t2

    def test_empty_plaintext(self, key):
        token = encrypt(b"", key)
        assert is_encrypted(token)

    def test_large_plaintext(self, key):
        big = os.urandom(100_000)
        token = encrypt(big, key)
        assert is_encrypted(token)

    def test_binary_plaintext(self, key):
        token = encrypt(bytes(range(256)), key)
        assert is_encrypted(token)

    def test_default_key_id_is_1(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = base64.b64decode(token[len(PREFIX):])
        key_id_bytes = blob[1: 1 + KEY_ID_SIZE]
        assert _decode_key_id(key_id_bytes) == "1"

    def test_custom_key_id_stored_in_token(self, key, plaintext):
        token = encrypt(plaintext, key, key_id="42")
        blob = base64.b64decode(token[len(PREFIX):])
        key_id_bytes = blob[1: 1 + KEY_ID_SIZE]
        assert _decode_key_id(key_id_bytes) == "42"

    def test_version_byte_is_1(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = base64.b64decode(token[len(PREFIX):])
        assert blob[0] == VERSION

    def test_nonce_is_12_bytes(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = base64.b64decode(token[len(PREFIX):])
        nonce = blob[1 + KEY_ID_SIZE: 1 + KEY_ID_SIZE + NONCE_SIZE]
        assert len(nonce) == NONCE_SIZE


# ---------------------------------------------------------------------------
# decrypt
# ---------------------------------------------------------------------------

class TestDecrypt:
    def test_roundtrip(self, key, plaintext):
        assert decrypt(encrypt(plaintext, key), key) == plaintext

    def test_roundtrip_empty_plaintext(self, key):
        assert decrypt(encrypt(b"", key), key) == b""

    def test_roundtrip_large_plaintext(self, key):
        big = os.urandom(100_000)
        assert decrypt(encrypt(big, key), key) == big

    def test_roundtrip_binary_plaintext(self, key):
        data = bytes(range(256))
        assert decrypt(encrypt(data, key), key) == data

    def test_roundtrip_unicode_content(self, key):
        data = "こんにちは 🔐 encrypted".encode()
        assert decrypt(encrypt(data, key), key) == data

    def test_wrong_key_raises(self, key, key2, plaintext):
        token = encrypt(plaintext, key)
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(token, key2)

    def test_missing_prefix_raises(self, key, plaintext):
        token = encrypt(plaintext, key)[len(PREFIX):]  # strip prefix
        with pytest.raises(ValueError, match="Not an encrypted token"):
            decrypt(token, key)

    def test_plain_json_raises(self, key):
        with pytest.raises(ValueError, match="Not an encrypted token"):
            decrypt('{"title": "hello"}', key)

    def test_empty_string_raises(self, key):
        with pytest.raises(ValueError, match="Not an encrypted token"):
            decrypt("", key)

    def test_invalid_base64_raises(self, key):
        with pytest.raises(ValueError, match="Invalid base64"):
            decrypt(PREFIX + "not-valid-base64!!!", key)

    def test_truncated_token_raises(self, key, plaintext):
        token = encrypt(plaintext, key)
        truncated = token[:len(PREFIX) + 10]
        with pytest.raises(ValueError):
            decrypt(truncated, key)

    def test_tampered_ciphertext_raises(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = bytearray(base64.b64decode(token[len(PREFIX):]))
        blob[-1] ^= 0xFF  # flip last byte of GCM tag
        tampered = PREFIX + base64.b64encode(bytes(blob)).decode()
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(tampered, key)

    def test_tampered_nonce_raises(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = bytearray(base64.b64decode(token[len(PREFIX):]))
        nonce_offset = 1 + KEY_ID_SIZE
        blob[nonce_offset] ^= 0xFF  # flip first nonce byte
        tampered = PREFIX + base64.b64encode(bytes(blob)).decode()
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(tampered, key)

    def test_tampered_payload_raises(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = bytearray(base64.b64decode(token[len(PREFIX):]))
        payload_offset = 1 + KEY_ID_SIZE + NONCE_SIZE
        if len(blob) > payload_offset:
            blob[payload_offset] ^= 0xFF
        tampered = PREFIX + base64.b64encode(bytes(blob)).decode()
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(tampered, key)

    def test_wrong_version_byte_raises(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = bytearray(base64.b64decode(token[len(PREFIX):]))
        blob[0] = 99  # unsupported version
        bad = PREFIX + base64.b64encode(bytes(blob)).decode()
        with pytest.raises(ValueError, match="Unknown encryption version"):
            decrypt(bad, key)

    def test_two_tokens_same_plaintext_both_decrypt(self, key, plaintext):
        # Different nonces — both must decrypt correctly
        t1 = encrypt(plaintext, key)
        t2 = encrypt(plaintext, key)
        assert decrypt(t1, key) == plaintext
        assert decrypt(t2, key) == plaintext

    def test_key_id_does_not_affect_decryption(self, key, plaintext):
        # key_id is metadata only — decryption uses the provided key regardless
        t1 = encrypt(plaintext, key, key_id="1")
        t2 = encrypt(plaintext, key, key_id="2")
        assert decrypt(t1, key) == plaintext
        assert decrypt(t2, key) == plaintext

    def test_31_byte_key_raises(self, plaintext):
        bad_key = os.urandom(31)
        with pytest.raises(Exception):
            encrypt(plaintext, bad_key)

    def test_33_byte_key_raises(self, plaintext):
        bad_key = os.urandom(33)
        with pytest.raises(Exception):
            encrypt(plaintext, bad_key)

    def test_16_byte_key_raises(self, plaintext):
        # AES-128 key — AESGCM accepts it but we require 256-bit keys
        bad_key = os.urandom(16)
        with pytest.raises(Exception):
            encrypt(plaintext, bad_key)


# ---------------------------------------------------------------------------
# Key length enforcement
# ---------------------------------------------------------------------------

class TestKeyLengthEnforcement:
    @pytest.mark.parametrize("length", [0, 1, 15, 16, 24, 31, 33, 64, 128])
    def test_non_32_byte_key_raises_on_encrypt(self, length, plaintext):
        with pytest.raises(ValueError, match="32 bytes"):
            encrypt(plaintext, os.urandom(length))

    def test_empty_key_raises(self, plaintext):
        with pytest.raises(ValueError):
            encrypt(plaintext, b"")

    def test_zero_bytes_key_raises(self, plaintext):
        with pytest.raises(ValueError):
            encrypt(plaintext, b"\x00" * 16)

    def test_all_zeros_32_byte_key_works(self, plaintext):
        # Weak key but structurally valid — should encrypt/decrypt
        weak_key = b"\x00" * 32
        token = encrypt(plaintext, weak_key)
        assert decrypt(token, weak_key) == plaintext

    def test_all_ff_32_byte_key_works(self, plaintext):
        key = b"\xff" * 32
        token = encrypt(plaintext, key)
        assert decrypt(token, key) == plaintext


# ---------------------------------------------------------------------------
# Ciphertext structure integrity
# ---------------------------------------------------------------------------

class TestCiphertextStructure:
    def test_minimum_token_length(self, key):
        # Empty plaintext: PREFIX + base64(1 + 4 + 12 + 0 + 16) = PREFIX + base64(33 bytes)
        token = encrypt(b"", key)
        header_bytes = 1 + KEY_ID_SIZE + NONCE_SIZE + 16  # version + key_id + nonce + tag
        min_b64_len = len(base64.b64encode(bytes(header_bytes)))
        assert len(token) >= len(PREFIX) + min_b64_len

    def test_token_grows_with_plaintext(self, key):
        short_token = encrypt(b"x" * 10, key)
        long_token = encrypt(b"x" * 1000, key)
        assert len(long_token) > len(short_token)

    def test_token_length_is_deterministic_for_same_size(self, key):
        # Same plaintext length → same token length (nonce is fixed size)
        t1 = encrypt(b"a" * 100, key)
        t2 = encrypt(b"b" * 100, key)
        assert len(t1) == len(t2)

    def test_stripping_prefix_gives_valid_base64(self, key, plaintext):
        token = encrypt(plaintext, key)
        b64_part = token[len(PREFIX):]
        # Should not raise
        base64.b64decode(b64_part)

    def test_blob_starts_with_version_1(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = base64.b64decode(token[len(PREFIX):])
        assert blob[0] == 1

    def test_all_header_bytes_present(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = base64.b64decode(token[len(PREFIX):])
        # version(1) + key_id(4) + nonce(12) + at least GCM tag(16)
        assert len(blob) >= 1 + KEY_ID_SIZE + NONCE_SIZE + 16

    def test_two_encryptions_have_different_nonces(self, key, plaintext):
        t1 = encrypt(plaintext, key)
        t2 = encrypt(plaintext, key)
        b1 = base64.b64decode(t1[len(PREFIX):])
        b2 = base64.b64decode(t2[len(PREFIX):])
        nonce1 = b1[1 + KEY_ID_SIZE: 1 + KEY_ID_SIZE + NONCE_SIZE]
        nonce2 = b2[1 + KEY_ID_SIZE: 1 + KEY_ID_SIZE + NONCE_SIZE]
        assert nonce1 != nonce2


# ---------------------------------------------------------------------------
# Tamper detection — exhaustive single-byte flips
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def test_flipping_every_ciphertext_byte_raises(self, key):
        plaintext = b"sensitive chat content"
        token = encrypt(plaintext, key)
        blob = bytearray(base64.b64decode(token[len(PREFIX):]))
        payload_start = 1 + KEY_ID_SIZE + NONCE_SIZE

        # Flip each byte in the ciphertext+tag region one at a time
        for i in range(payload_start, len(blob)):
            mutated = bytearray(blob)
            mutated[i] ^= 0xFF
            bad_token = PREFIX + base64.b64encode(bytes(mutated)).decode()
            with pytest.raises(ValueError):
                decrypt(bad_token, key)

    def test_flipping_every_nonce_byte_raises(self, key):
        plaintext = b"sensitive chat content"
        token = encrypt(plaintext, key)
        blob = bytearray(base64.b64decode(token[len(PREFIX):]))
        nonce_start = 1 + KEY_ID_SIZE

        for i in range(nonce_start, nonce_start + NONCE_SIZE):
            mutated = bytearray(blob)
            mutated[i] ^= 0x01
            bad_token = PREFIX + base64.b64encode(bytes(mutated)).decode()
            with pytest.raises(ValueError):
                decrypt(bad_token, key)

    def test_appending_byte_raises(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = base64.b64decode(token[len(PREFIX):]) + b"\x00"
        bad_token = PREFIX + base64.b64encode(blob).decode()
        with pytest.raises(ValueError):
            decrypt(bad_token, key)

    def test_prepending_byte_to_payload_raises(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = b"\x00" + base64.b64decode(token[len(PREFIX):])
        bad_token = PREFIX + base64.b64encode(blob).decode()
        with pytest.raises(ValueError):
            decrypt(bad_token, key)

    def test_truncating_tag_raises(self, key, plaintext):
        token = encrypt(plaintext, key)
        blob = base64.b64decode(token[len(PREFIX):])
        truncated = blob[:-8]  # remove half the GCM tag
        bad_token = PREFIX + base64.b64encode(truncated).decode()
        with pytest.raises(ValueError):
            decrypt(bad_token, key)

    def test_swapping_two_tokens_raises(self, key):
        # Token produced for msg A must not decrypt as msg B
        msg_a = b"message from alice"
        msg_b = b"message from bob"
        token_a = encrypt(msg_a, key)
        token_b = encrypt(msg_b, key)
        assert decrypt(token_a, key) == msg_a
        assert decrypt(token_b, key) == msg_b
        with pytest.raises(ValueError):
            # token_b cannot be decrypted with blob from token_a's nonce — verify isolation
            blob_a = base64.b64decode(token_a[len(PREFIX):])
            blob_b = base64.b64decode(token_b[len(PREFIX):])
            # swap the ciphertext region while keeping token_a's nonce
            nonce_end = 1 + KEY_ID_SIZE + NONCE_SIZE
            swapped = blob_a[:nonce_end] + blob_b[nonce_end:]
            bad = PREFIX + base64.b64encode(swapped).decode()
            decrypt(bad, key)


# ---------------------------------------------------------------------------
# Key isolation — tokens from one key cannot cross to another
# ---------------------------------------------------------------------------

class TestKeyIsolation:
    def test_token_from_key1_cannot_decrypt_with_key2(self, key, key2, plaintext):
        token = encrypt(plaintext, key)
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(token, key2)

    def test_token_from_key2_cannot_decrypt_with_key1(self, key, key2, plaintext):
        token = encrypt(plaintext, key2)
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(token, key)

    def test_many_keys_each_isolate_correctly(self, plaintext):
        keys = [os.urandom(32) for _ in range(10)]
        tokens = [encrypt(plaintext, k) for k in keys]
        for i, (token, correct_key) in enumerate(zip(tokens, keys)):
            assert decrypt(token, correct_key) == plaintext
            for j, wrong_key in enumerate(keys):
                if j != i:
                    with pytest.raises(ValueError):
                        decrypt(token, wrong_key)


# ---------------------------------------------------------------------------
# Plaintext boundary conditions
# ---------------------------------------------------------------------------

class TestPlaintextBoundaries:
    def test_single_byte_plaintext(self, key):
        assert decrypt(encrypt(b"\x00", key), key) == b"\x00"

    def test_single_byte_all_values(self, key):
        for byte_val in range(256):
            data = bytes([byte_val])
            assert decrypt(encrypt(data, key), key) == data

    def test_null_bytes_in_plaintext(self, key):
        data = b"\x00" * 100
        assert decrypt(encrypt(data, key), key) == data

    def test_plaintext_with_enc1_prefix_string(self, key):
        # Content that looks like a token prefix should still round-trip
        data = b"ENC1:somebase64data"
        assert decrypt(encrypt(data, key), key) == data

    def test_very_long_plaintext_1mb(self, key):
        data = os.urandom(1024 * 1024)
        assert decrypt(encrypt(data, key), key) == data

    def test_repeating_pattern_plaintext(self, key):
        data = b"ABCD" * 10000
        assert decrypt(encrypt(data, key), key) == data

    def test_json_chat_payload(self, key):
        import json
        payload = json.dumps({
            "title": "Test Chat",
            "history": {
                "messages": {
                    "msg-1": {"role": "user", "content": "hello"},
                    "msg-2": {"role": "assistant", "content": "hi there"},
                }
            }
        }).encode()
        assert decrypt(encrypt(payload, key), key) == payload


# ---------------------------------------------------------------------------
# is_encrypted edge cases
# ---------------------------------------------------------------------------

class TestIsEncryptedEdgeCases:
    def test_list_returns_false(self):
        assert is_encrypted([]) is False  # type: ignore

    def test_dict_returns_false(self):
        assert is_encrypted({}) is False  # type: ignore

    def test_bytes_returns_false(self, key, plaintext):
        # bytes object — not a str
        assert is_encrypted(plaintext) is False  # type: ignore

    def test_prefix_only_no_payload_returns_true(self):
        # Structurally just the prefix with no body — is_encrypted only checks prefix
        assert is_encrypted(PREFIX) is True

    def test_prefix_with_garbage_returns_true(self):
        # is_encrypted does not validate — only decrypt does
        assert is_encrypted(PREFIX + "not-valid-base64!!!") is True

    def test_lowercase_prefix_returns_false(self):
        assert is_encrypted("enc1:abc") is False

    def test_whitespace_before_prefix_returns_false(self):
        assert is_encrypted(" " + PREFIX + "abc") is False


# ---------------------------------------------------------------------------
# Determinism and independence
# ---------------------------------------------------------------------------

class TestDeterminismAndIndependence:
    def test_100_encryptions_all_unique(self, key, plaintext):
        tokens = {encrypt(plaintext, key) for _ in range(100)}
        assert len(tokens) == 100  # all unique due to random nonce

    def test_100_decryptions_all_correct(self, key, plaintext):
        token = encrypt(plaintext, key)
        for _ in range(100):
            assert decrypt(token, key) == plaintext

    def test_encrypt_does_not_modify_key(self, plaintext):
        key = bytearray(os.urandom(32))
        key_copy = bytes(key)
        encrypt(plaintext, bytes(key))
        assert bytes(key) == key_copy

    def test_encrypt_does_not_modify_plaintext(self, key):
        plaintext = bytearray(b"original content")
        plaintext_copy = bytes(plaintext)
        encrypt(bytes(plaintext), key)
        assert bytes(plaintext) == plaintext_copy
