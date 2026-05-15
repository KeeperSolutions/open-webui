"""
Backfill script — encrypt all plaintext chat rows.

Run locally:
    cd backend
    python -m open_webui.scripts.backfill_chat_encryption

Run in production (Cloud Run Job):
    python -m open_webui.scripts.backfill_chat_encryption

Idempotent: rows already starting with ENC1: are skipped. Safe to re-run after a crash.
"""

import json
import logging
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / "backend" / ".env")
import sys

from sqlalchemy import text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BATCH_SIZE = 100
SLEEP_BETWEEN_BATCHES = 0.05  # seconds — keeps DB pressure low

# Cast chat to TEXT for the LIKE filter — required on PostgreSQL where chat is
# a JSON/JSONB column; harmless on SQLite where it is already TEXT.
_SELECT_PLAINTEXT = text(
    "SELECT id, chat FROM chat "
    "WHERE CAST(chat AS TEXT) NOT LIKE 'ENC1:%' "
    "ORDER BY updated_at DESC "
    "LIMIT :limit"
)

_COUNT_PLAINTEXT = text(
    "SELECT COUNT(*) FROM chat WHERE CAST(chat AS TEXT) NOT LIKE 'ENC1:%'"
)


def _to_bytes(raw) -> bytes:
    """Return the canonical UTF-8 bytes to encrypt.

    raw may be:
    - str  — already serialized JSON (SQLite TEXT column)
    - dict/list — already deserialized (PostgreSQL JSON/JSONB column)
    """
    if isinstance(raw, (dict, list)):
        return json.dumps(raw).encode()
    return raw.encode()


def run():
    from open_webui import kms
    from open_webui import crypto
    from open_webui.internal.db import get_db

    if not kms.is_enabled():
        log.error("CHAT_ENCRYPTION_ENABLED is not set to true — aborting.")
        sys.exit(1)

    kms.load_key()
    key = kms.get_key()

    total_encrypted = 0
    total_skipped = 0
    total_failed = 0

    log.info("Starting chat encryption backfill...")

    with get_db() as db:
        total_rows = db.execute(_COUNT_PLAINTEXT).scalar()
    log.info(f"Plaintext rows to encrypt: {total_rows}")

    batch_num = 0
    while True:
        with get_db() as db:
            rows = db.execute(_SELECT_PLAINTEXT, {"limit": BATCH_SIZE}).fetchall()

            if not rows:
                break

            batch_encrypted = 0
            batch_skipped = 0
            updates = []

            for row in rows:
                chat_id, raw = row.id, row.chat

                if raw is None:
                    batch_skipped += 1
                    total_skipped += 1
                    continue

                try:
                    ciphertext = crypto.encrypt(_to_bytes(raw), key)
                    updates.append({"chat": ciphertext, "id": chat_id})
                    batch_encrypted += 1
                    total_encrypted += 1
                except Exception as e:
                    log.error(f"Failed to encrypt chat {chat_id}: {e}")
                    total_failed += 1

            if updates:
                db.execute(
                    text("UPDATE chat SET chat = :chat WHERE id = :id"),
                    updates,
                )
                db.commit()

        batch_num += 1
        log.info(
            f"Batch {batch_num} | encrypted={batch_encrypted} skipped={batch_skipped} "
            f"| total so far={total_encrypted}/{total_rows}"
        )

        if len(rows) < BATCH_SIZE:
            break

        time.sleep(SLEEP_BETWEEN_BATCHES)

    log.info(
        f"Backfill complete — encrypted={total_encrypted} skipped={total_skipped} failed={total_failed}"
    )
    if total_failed > 0:
        log.error(f"{total_failed} rows failed — re-run to retry.")
        sys.exit(1)


if __name__ == "__main__":
    run()
