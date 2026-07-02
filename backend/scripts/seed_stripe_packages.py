#!/usr/bin/env python3
"""
Seed stripe_packages table with subscription plan definitions.

Usage (from backend/ directory):
    python scripts/seed_stripe_packages.py

Idempotent — skips rows that already exist.
"""

import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

PACKAGES = [
    {
        "id": "pkg_pro",
        "name": "Pro",
        "plan_tier": "pro",
        "stripe_price_id": "price_1TPe2LHM5MSzOp4WlwOsugVN",
        "price_eur": 15.0,
        "credits": 1300,
        "seat_count": None,
    },
    {
        "id": "pkg_premium",
        "name": "Premium",
        "plan_tier": "premium",
        "stripe_price_id": "price_1To0aHHM5MSzOp4WDroBKkmT",
        "price_eur": 45.0,
        "credits": 3800,
        "seat_count": None,
    },
    {
        "id": "pkg_team_10",
        "name": "Team – 10 seats",
        "plan_tier": "team",
        "stripe_price_id": "price_1ToNI9HM5MSzOp4Wa5fjWFGH",
        "price_eur": 229.0,
        "credits": 10000,
        "seat_count": 10,
    },
    {
        "id": "pkg_team_20",
        "name": "Team – 20 seats",
        "plan_tier": "team",
        "stripe_price_id": "price_1ToNIAHM5MSzOp4WKCQeFLan",
        "price_eur": 379.0,
        "credits": 20000,
        "seat_count": 20,
    },
    {
        "id": "pkg_team_50",
        "name": "Team – 50 seats",
        "plan_tier": "team",
        "stripe_price_id": "price_1ToNIBHM5MSzOp4WLadXlLtY",
        "price_eur": 829.0,
        "credits": 50000,
        "seat_count": 50,
    },
]


def seed():
    from open_webui.internal.db import get_db
    from open_webui.models.stripe_packages import StripePackage

    now = int(time.time())
    inserted = 0
    skipped = 0

    with get_db() as db:
        for pkg in PACKAGES:
            existing = db.query(StripePackage).filter_by(id=pkg["id"]).first()
            if existing:
                print(f"[seed] SKIP {pkg['id']} — already exists")
                skipped += 1
                continue

            row = StripePackage(
                id=pkg["id"],
                name=pkg["name"],
                plan_tier=pkg["plan_tier"],
                stripe_price_id=pkg["stripe_price_id"],
                price_eur=pkg["price_eur"],
                credits=pkg["credits"],
                seat_count=pkg["seat_count"],
                is_active=True,
                created_at=now,
            )
            db.add(row)
            print(f"[seed] INSERT {pkg['id']} ({pkg['name']}) price_id={pkg['stripe_price_id']}")
            inserted += 1

        db.commit()

    print(f"\n[seed] Done — {inserted} inserted, {skipped} skipped.")


if __name__ == "__main__":
    seed()
