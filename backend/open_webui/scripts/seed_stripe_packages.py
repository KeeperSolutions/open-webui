"""Seed stripe_packages table with plan definitions from .env.

Run from the backend/ directory:
    python -m open_webui.scripts.seed_stripe_packages

Skips rows that already exist (idempotent).
"""
import os
import sys
import time
import uuid

# Allow running from backend/ root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def seed():
    packages = [
        {
            "id": "pkg_pro",
            "name": "Pro",
            "plan_tier": "pro",
            "stripe_price_id": os.environ.get("STRIPE_PRICE_ID", ""),
            "price_eur": 15.0,
            "credits": 1300,
            "seat_count": None,
        },
        {
            "id": "pkg_team_10",
            "name": "Team – 10 seats",
            "plan_tier": "team",
            "stripe_price_id": os.environ.get("STRIPE_PRICE_TEAM_10", ""),
            "price_eur": 229.0,
            "credits": 10000,
            "seat_count": 10,
        },
        {
            "id": "pkg_team_20",
            "name": "Team – 20 seats",
            "plan_tier": "team",
            "stripe_price_id": os.environ.get("STRIPE_PRICE_TEAM_20", ""),
            "price_eur": 379.0,
            "credits": 20000,
            "seat_count": 20,
        },
        {
            "id": "pkg_team_50",
            "name": "Team – 50 seats",
            "plan_tier": "team",
            "stripe_price_id": os.environ.get("STRIPE_PRICE_TEAM_50", ""),
            "price_eur": 829.0,
            "credits": 50000,
            "seat_count": 50,
        },
    ]
    from open_webui.internal.db import get_db
    from open_webui.models.stripe_packages import StripePackage

    now = int(time.time())
    inserted = 0
    skipped = 0

    with get_db() as db:
        for pkg in packages:
            if not pkg["stripe_price_id"]:
                print(f"[seed] SKIP {pkg['id']} — stripe_price_id not set in env")
                skipped += 1
                continue

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
    # Load .env — search upward from CWD
    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    seed()
