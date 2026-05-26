"""
Key management — loads the system encryption key at startup.

Two backends controlled by BACKEND_ENCRYPTION env var:
  local    — key read from CHAT_ENCRYPTION_KEY env var (dev / SQLite)
  gcp_kms  — key read from GCP Secret Manager using KMS_SECRET_NAME env var (production)

The loaded key is a 32-byte AES-256 key held in memory for the lifetime of
the process — no per-request key fetches.

Required env vars:
  BACKEND_ENCRYPTION       — "local" or "gcp_kms" (default: "local")
  CHAT_ENCRYPTION_ENABLED  — "true" or "false" (default: "false")
  CHAT_ENCRYPTION_KEY      — base64-encoded 32-byte key (required when BACKEND_ENCRYPTION=local)
  KMS_SECRET_NAME          — full GCP secret resource name (required when BACKEND_ENCRYPTION=gcp_kms)
                             e.g. projects/my-project/secrets/chat-encryption-key/versions/latest
"""

import os
import base64
import logging

log = logging.getLogger(__name__)

_key: bytes | None = None


def _decode_key(raw: str, env_var: str) -> bytes:
    """Validate and decode a base64-encoded 32-byte key."""
    import re
    if not raw or raw != raw.strip():
        raise RuntimeError(f"{env_var} is not valid base64")
    if not re.fullmatch(r"[A-Za-z0-9+/\-_]+=*", raw):
        raise RuntimeError(f"{env_var} is not valid base64")
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception:
        raise RuntimeError(f"{env_var} is not valid base64")
    if len(key) != 32:
        raise RuntimeError(f"{env_var} must decode to 32 bytes, got {len(key)}")
    return key


def _load_local() -> bytes:
    raw = os.environ.get("CHAT_ENCRYPTION_KEY", "")
    if not raw:
        raise RuntimeError(
            "CHAT_ENCRYPTION_KEY env var is required when BACKEND_ENCRYPTION=local"
        )
    return _decode_key(raw, "CHAT_ENCRYPTION_KEY")


def _load_gcp() -> bytes:
    secret_name = os.environ.get("KMS_SECRET_NAME", "")
    if not secret_name:
        raise RuntimeError(
            "KMS_SECRET_NAME env var is required when BACKEND_ENCRYPTION=gcp_kms"
        )
    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_name)
    raw = response.payload.data.decode("utf-8").strip()
    key = _decode_key(raw, "KMS_SECRET_NAME")
    return key


def load_key() -> None:
    """Load the encryption key into memory. Call once at application startup."""
    global _key
    backend = os.environ.get("BACKEND_ENCRYPTION", "local").lower()
    if backend == "gcp_kms":
        _key = _load_gcp()
        log.info("[crypto] Loaded encryption key from GCP Secret Manager")
    elif backend == "local":
        _key = _load_local()
        log.info("[crypto] Loaded encryption key from environment variable")
    else:
        raise RuntimeError(
            f"Unknown BACKEND_ENCRYPTION: {backend!r} (expected 'local' or 'gcp_kms')"
        )


def get_key() -> bytes:
    """Return the loaded key. Raises if load_key() was never called."""
    if _key is None:
        raise RuntimeError("Encryption key not loaded — call load_key() at startup")
    return _key


def is_enabled() -> bool:
    """Return True if chat encryption is enabled."""
    return os.environ.get("CHAT_ENCRYPTION_ENABLED", "false").lower() == "true"
