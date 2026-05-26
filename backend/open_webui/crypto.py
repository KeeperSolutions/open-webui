"""
Application-layer encryption for chat content.

Ciphertext format (base64-encoded, prefixed with ENC1:):
  [1 byte: version][4 bytes: key_id][12 bytes: nonce][variable: ciphertext][16 bytes: GCM tag]

The ENC1: prefix lets the DB layer distinguish encrypted rows from legacy plaintext
during the migration window.
"""

import os
import base64
import struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
PREFIX = "ENC1:"
NONCE_SIZE = 12
KEY_ID_SIZE = 4


def _encode_key_id(key_id: str) -> bytes:
    # Store key_id as 4-byte big-endian int; key_id strings are expected to be
    # numeric version numbers ("1", "2", ...).
    return struct.pack(">I", int(key_id))


def _decode_key_id(raw: bytes) -> str:
    return str(struct.unpack(">I", raw)[0])


def encrypt(plaintext: bytes, key: bytes, key_id: str = "1") -> str:
    """Encrypt plaintext bytes, return a prefixed base64 string."""
    if len(key) != 32:
        raise ValueError(f"Key must be 32 bytes (AES-256), got {len(key)}")
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)

    blob = (
        bytes([VERSION])
        + _encode_key_id(key_id)
        + nonce
        + ciphertext_with_tag
    )
    return PREFIX + base64.b64encode(blob).decode()


def decrypt(token: str, key: bytes) -> bytes:
    """Decrypt a token produced by encrypt(). Raises ValueError on tamper or bad format."""
    if not token.startswith(PREFIX):
        raise ValueError("Not an encrypted token")

    try:
        blob = base64.b64decode(token[len(PREFIX):])
    except Exception:
        raise ValueError("Invalid base64 in encrypted token")

    # 1 (version) + 4 (key_id) + 12 (nonce) + 16 (GCM tag) = minimum 33 bytes
    MIN_BLOB_SIZE = 1 + KEY_ID_SIZE + NONCE_SIZE + 16
    if len(blob) < MIN_BLOB_SIZE:
        raise ValueError("Invalid token: payload too short")

    offset = 0
    version = blob[offset]
    offset += 1

    if version != VERSION:
        raise ValueError(f"Unknown encryption version: {version}")

    _key_id = _decode_key_id(blob[offset: offset + KEY_ID_SIZE])
    offset += KEY_ID_SIZE

    nonce = blob[offset: offset + NONCE_SIZE]
    offset += NONCE_SIZE

    ciphertext_with_tag = blob[offset:]

    try:
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except Exception:
        raise ValueError("Decryption failed — invalid key or tampered ciphertext")


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)
