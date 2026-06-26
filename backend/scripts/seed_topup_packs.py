#!/usr/bin/env python3
"""
Seed top-up packs into the database.

Usage:
    python scripts/seed_topup_packs.py

The script reads the following environment variables (or uses defaults):
    TOPUP_PACKS   - JSON array of pack definitions
    Default packs use Stripe TEST mode price IDs.
"""

import json
import os
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from open_webui.internal.db import get_db
from open_webui.models.topup import TopupPacks, TopupPack

DEFAULT_PACKS = [
    {"id": "pack_25",   "credits": 25,   "price_eur": 5.0,  "stripe_price_id": "price_1Tlpk5HM5MSzOp4WtgaUKBZT"},
    {"id": "pack_50",   "credits": 50,   "price_eur": 10.0, "stripe_price_id": "price_1Tlpk6HM5MSzOp4WhrWzfWjV"},
    {"id": "pack_100",  "credits": 100,  "price_eur": 20.0, "stripe_price_id": "price_1Tlpk6HM5MSzOp4WL4U7MfPr"},
    {"id": "pack_250",  "credits": 250,  "price_eur": 50.0, "stripe_price_id": "price_1Tlpk7HM5MSzOp4WfhVewCmu"},
    {"id": "pack_500",  "credits": 500,  "price_eur": 100.0, "stripe_price_id": "price_1Tlpk8HM5MSzOp4WtBlwTANq"},
    {"id": "pack_1000", "credits": 1000, "price_eur": 200.0, "stripe_price_id": "price_1Tlpk8HM5MSzOp4WyvnvqS2a"},
    {"id": "pack_2000", "credits": 2000, "price_eur": 400.0, "stripe_price_id": "price_1Tlpk9HM5MSzOp4WV0ssSnxv"},
]

def seed_topup_packs(packs=None):
    if packs is None:
        packs_json = os.environ.get("TOPUP_PACKS")
        if packs_json:
            packs = json.loads(packs_json)
        else:
            packs = DEFAULT_PACKS

    created = 0
    updated = 0

    with get_db() as db:
        # Only seed if no packs exist yet (lightweight dev check)
        existing_count = db.query(TopupPack).count()
        if existing_count > 0:
            return 0, 0

        for p in packs:
            existing = TopupPacks.get_by_id(p["id"])
            now = int(time.time())

            if existing:
                # Update stripe_price_id if different (allows switching test/live)
                if existing.stripe_price_id != p["stripe_price_id"]:
                    db.query(TopupPack).filter_by(id=p["id"]).update({
                        "stripe_price_id": p["stripe_price_id"],
                        "updated_at": now
                    })
                    updated += 1
            else:
                pack = TopupPack(
                    id=p["id"],
                    credits=p["credits"],
                    price_eur=p["price_eur"],
                    stripe_price_id=p["stripe_price_id"],
                    created_at=now,
                    updated_at=now,
                )
                db.add(pack)
                created += 1

        db.commit()

    print(f"Seeded top-up packs: {created} created, {updated} updated")
    return created, updated


if __name__ == "__main__":
    seed_topup_packs()
