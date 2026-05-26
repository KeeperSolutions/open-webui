"""
Backfill script — decrypt all encrypted chat rows back to plaintext JSON.

Use this before disabling encryption (CHAT_ENCRYPTION_ENABLED=false) on a
database that already has ENC1: rows. Without running this first, the app
will raise a RuntimeError when trying to read any encrypted row.

Run locally:
    cd backend
    python -m open_webui.scripts.backfill_chat_decryption

Run in production (Cloud Run Job):
    python -m open_webui.scripts.backfill_chat_decryption

Idempotent: rows that don't start with ENC1: are skipped. Safe to re-run after a crash.
"""

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

_SELECT_ENCRYPTED = text(
    "SELECT id, chat FROM chat "
    "WHERE CAST(chat AS TEXT) LIKE 'ENC1:%' "
    "ORDER BY updated_at DESC "
    "LIMIT :limit"
)

_COUNT_ENCRYPTED = text(
    "SELECT COUNT(*) FROM chat WHERE CAST(chat AS TEXT) LIKE 'ENC1:%'"
)


def run():
    from open_webui import kms
    from open_webui import crypto
    from open_webui.internal.db import get_db

    if not kms.is_enabled():
        log.error("CHAT_ENCRYPTION_ENABLED is not set to true — cannot load key, aborting.")
        log.error("Set CHAT_ENCRYPTION_ENABLED=true and provide the key to run decryption.")
        sys.exit(1)

    kms.load_key()
    key = kms.get_key()

    total_decrypted = 0
    total_skipped = 0
    total_failed = 0

    log.info("Starting chat decryption backfill...")

    with get_db() as db:
        total_rows = db.execute(_COUNT_ENCRYPTED).scalar()
    log.info(f"Encrypted rows to decrypt: {total_rows}")

    if total_rows == 0:
        log.info("No encrypted rows found — nothing to do.")
        return

    batch_num = 0
    while True:
        with get_db() as db:
            rows = db.execute(_SELECT_ENCRYPTED, {"limit": BATCH_SIZE}).fetchall()

            if not rows:
                break

            batch_decrypted = 0
            batch_skipped = 0
            updates = []

            for row in rows:
                chat_id, raw = row.id, row.chat

                if raw is None:
                    batch_skipped += 1
                    total_skipped += 1
                    continue

                if not isinstance(raw, str) or not raw.startswith("ENC1:"):
                    batch_skipped += 1
                    total_skipped += 1
                    continue

                try:
                    plaintext = crypto.decrypt(raw, key).decode()
                    updates.append({"chat": plaintext, "id": chat_id})
                    batch_decrypted += 1
                    total_decrypted += 1
                except Exception as e:
                    log.error(f"Failed to decrypt chat {chat_id}: {e}")
                    total_failed += 1

            if updates:
                db.execute(
                    text("UPDATE chat SET chat = :chat WHERE id = :id"),
                    updates,
                )
                db.commit()

        batch_num += 1
        log.info(
            f"Batch {batch_num} | decrypted={batch_decrypted} skipped={batch_skipped} "
            f"| total so far={total_decrypted}/{total_rows}"
        )

        if len(rows) < BATCH_SIZE:
            break

        time.sleep(SLEEP_BETWEEN_BATCHES)

    log.info(
        f"Decryption complete — decrypted={total_decrypted} skipped={total_skipped} failed={total_failed}"
    )
    if total_failed > 0:
        log.error(f"{total_failed} rows failed — re-run to retry.")
        sys.exit(1)


if __name__ == "__main__":
    run()
