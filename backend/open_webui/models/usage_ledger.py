import datetime as dt
import logging
import time
import uuid
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Float, Index, Integer, Text, case, func, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from open_webui.internal.db import Base, get_db

log = logging.getLogger(__name__)


####################
# UsageLedger DB Schema
####################


class UsageLedger(Base):
    __tablename__ = "usage_ledger"

    id = Column(Text, primary_key=True)
    langfuse_observation_id = Column(Text, unique=True, nullable=False)
    user_id = Column(Text, nullable=False)   # email
    model = Column(Text, nullable=False)

    tokens_input = Column(Integer, default=0, nullable=False)
    tokens_output = Column(Integer, default=0, nullable=False)
    tokens_total = Column(Integer, default=0, nullable=False)

    cost_usd = Column(Float, nullable=True)      # null = no Langfuse pricing for model
    eur_usd_rate = Column(Float, nullable=True)  # USD per 1 EUR at time of call
    cost_eur = Column(Float, nullable=True)      # null if cost_usd or rate is unavailable

    observed_at = Column(BigInteger, nullable=False)  # unix epoch of LLM call startTime
    synced_at = Column(BigInteger, nullable=False)    # unix epoch of ledger insertion

    __table_args__ = (
        Index("ix_usage_ledger_user_id", "user_id"),
        Index("ix_usage_ledger_observed_at", "observed_at"),
    )


class UsageLedgerModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    langfuse_observation_id: str
    user_id: str
    model: str

    tokens_input: int = 0
    tokens_output: int = 0
    tokens_total: int = 0

    cost_usd: Optional[float] = None
    eur_usd_rate: Optional[float] = None
    cost_eur: Optional[float] = None

    observed_at: int
    synced_at: int


####################
# Table accessor
####################


def _month_start_epoch() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    return int(dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc).timestamp())


class UsageLedgerTable:
    def __init__(self) -> None:
        # Cached after first positive DB check; avoids repeated MAX() scans.
        # Instance attribute (not module-level) so tests can reset it per-instance.
        self._has_data: bool = False

    def get_max_observed_at(self) -> Optional[int]:
        with get_db() as db:
            result = db.query(func.max(UsageLedger.observed_at)).scalar()
            return int(result) if result is not None else None

    def is_ledger_ready(self) -> bool:
        """True if the ledger has ever had data. Cached after first positive check."""
        if self._has_data:
            return True
        has_data = self.get_max_observed_at() is not None
        if has_data:
            self._has_data = True
        return has_data

    def bulk_insert_ignore(self, rows: List[Dict]) -> int:
        """Insert rows, silently skipping duplicates on langfuse_observation_id. Returns inserted count."""
        if not rows:
            return 0
        now_ts = int(time.time())
        records = [
            {
                "id": str(uuid.uuid4()),
                "langfuse_observation_id": r["langfuse_observation_id"],
                "user_id": r["user_id"],
                "model": r["model"],
                "tokens_input": r.get("tokens_input", 0) or 0,
                "tokens_output": r.get("tokens_output", 0) or 0,
                "tokens_total": r.get("tokens_total", 0) or 0,
                "cost_usd": r.get("cost_usd"),
                "eur_usd_rate": r.get("eur_usd_rate"),
                "cost_eur": r.get("cost_eur"),
                "observed_at": r["observed_at"],
                "synced_at": now_ts,
            }
            for r in rows
        ]
        with get_db() as db:
            try:
                # PostgreSQL: ON CONFLICT DO NOTHING
                stmt = pg_insert(UsageLedger).values(records).on_conflict_do_nothing(
                    index_elements=["langfuse_observation_id"]
                )
                result = db.execute(stmt)
                db.commit()
                inserted = result.rowcount if result.rowcount >= 0 else len(records)
            except Exception:
                # SQLite fallback: INSERT OR IGNORE
                db.rollback()
                inserted = 0
                for record in records:
                    try:
                        result = db.execute(
                            insert(UsageLedger).prefix_with("OR IGNORE").values(**record)
                        )
                        inserted += result.rowcount
                    except Exception:
                        pass
                db.commit()
        if inserted > 0:
            self._has_data = True
        return inserted

    def bulk_upsert_costs(self, rows: List[Dict]) -> int:
        """Update cost columns for existing rows where cost_eur is currently NULL.

        Used by the nightly deep rescan to backfill pricing that Langfuse added after
        the original insert. Only overwrites a row when the stored cost_eur IS NULL and
        the incoming cost_usd AND cost_eur are both NOT NULL — priced rows are never
        touched, and rows where ECB was also unavailable (cost_eur=None) are skipped.
        Uses a single bulk CASE UPDATE to avoid N+1 round-trips.
        Returns the number of rows updated.
        """
        if not rows:
            return 0
        # Require both cost_usd and cost_eur to be non-None — skips rows where ECB
        # was also unavailable during the rescan, which would write NULL back onto NULL.
        costed = [r for r in rows if r.get("cost_usd") is not None and r.get("cost_eur") is not None]
        if not costed:
            return 0

        now_ts = int(time.time())
        obs_ids = [r["langfuse_observation_id"] for r in costed]

        with get_db() as db:
            result = db.execute(
                UsageLedger.__table__.update()
                .where(
                    UsageLedger.langfuse_observation_id.in_(obs_ids),
                    UsageLedger.cost_eur.is_(None),
                )
                .values(
                    cost_usd=case(
                        {r["langfuse_observation_id"]: r["cost_usd"] for r in costed},
                        value=UsageLedger.langfuse_observation_id,
                    ),
                    eur_usd_rate=case(
                        {r["langfuse_observation_id"]: r.get("eur_usd_rate") for r in costed},
                        value=UsageLedger.langfuse_observation_id,
                    ),
                    cost_eur=case(
                        {r["langfuse_observation_id"]: r["cost_eur"] for r in costed},
                        value=UsageLedger.langfuse_observation_id,
                    ),
                    synced_at=now_ts,
                )
            )
            db.commit()
            updated = result.rowcount if result.rowcount >= 0 else 0

        if updated > 0:
            self._has_data = True
        return updated

    def get_cost_eur_for_user_current_month(self, user_id: str) -> float:
        with get_db() as db:
            result = (
                db.query(func.coalesce(func.sum(UsageLedger.cost_eur), 0.0))
                .filter(
                    UsageLedger.user_id == user_id,
                    UsageLedger.observed_at >= _month_start_epoch(),
                    UsageLedger.cost_eur.isnot(None),
                )
                .scalar()
            )
            return float(result or 0.0)

    def get_cost_eur_for_user_since(self, user_id: str, since_ts: int) -> float:
        with get_db() as db:
            result = (
                db.query(func.coalesce(func.sum(UsageLedger.cost_eur), 0.0))
                .filter(
                    UsageLedger.user_id == user_id,
                    UsageLedger.observed_at >= since_ts,
                    UsageLedger.cost_eur.isnot(None),
                )
                .scalar()
            )
            return float(result or 0.0)

    def get_cost_eur_for_users_current_month(self, user_ids: List[str]) -> Dict[str, float]:
        if not user_ids:
            return {}
        month_start = _month_start_epoch()
        with get_db() as db:
            rows = (
                db.query(UsageLedger.user_id, func.sum(UsageLedger.cost_eur))
                .filter(
                    UsageLedger.user_id.in_(user_ids),
                    UsageLedger.observed_at >= month_start,
                    UsageLedger.cost_eur.isnot(None),
                )
                .group_by(UsageLedger.user_id)
                .all()
            )
        return {uid: float(total or 0.0) for uid, total in rows}

    def get_cost_usd_for_user_current_month(self, user_id: str) -> float:
        with get_db() as db:
            result = (
                db.query(func.coalesce(func.sum(UsageLedger.cost_usd), 0.0))
                .filter(
                    UsageLedger.user_id == user_id,
                    UsageLedger.observed_at >= _month_start_epoch(),
                    UsageLedger.cost_usd.isnot(None),
                )
                .scalar()
            )
            return float(result or 0.0)

    def get_exchange_rates_current_month(self, user_id: str) -> List[Dict]:
        """Return distinct EUR/USD rates used this month with first/last observed date."""
        month_start = _month_start_epoch()
        with get_db() as db:
            rows = (
                db.query(
                    UsageLedger.eur_usd_rate,
                    func.min(UsageLedger.observed_at).label("first_used"),
                    func.max(UsageLedger.observed_at).label("last_used"),
                )
                .filter(
                    UsageLedger.user_id == user_id,
                    UsageLedger.observed_at >= month_start,
                    UsageLedger.eur_usd_rate.isnot(None),
                )
                .group_by(UsageLedger.eur_usd_rate)
                .order_by(func.min(UsageLedger.observed_at))
                .all()
            )
        return [
            {
                "usd_per_eur": round(r.eur_usd_rate, 6),
                "from": r.first_used,
                "to": r.last_used,
            }
            for r in rows
        ]

    def get_tokens_for_user_current_month(self, user_id: str) -> int:
        with get_db() as db:
            result = (
                db.query(func.coalesce(func.sum(UsageLedger.tokens_total), 0))
                .filter(
                    UsageLedger.user_id == user_id,
                    UsageLedger.observed_at >= _month_start_epoch(),
                )
                .scalar()
            )
            return int(result or 0)

    def get_models_used_bulk_current_month(self, user_ids: List[str]) -> Dict[str, List[str]]:
        """Return {user_id: [top-3 model names by cost]} for all given users in one query."""
        if not user_ids:
            return {}
        month_start = _month_start_epoch()
        with get_db() as db:
            rows = (
                db.query(
                    UsageLedger.user_id,
                    UsageLedger.model,
                    func.coalesce(func.sum(UsageLedger.cost_eur), 0.0).label("cost_eur"),
                )
                .filter(
                    UsageLedger.user_id.in_(user_ids),
                    UsageLedger.observed_at >= month_start,
                )
                .group_by(UsageLedger.user_id, UsageLedger.model)
                .order_by(func.coalesce(func.sum(UsageLedger.cost_eur), 0.0).desc())
                .all()
            )
        result: Dict[str, List[str]] = {}
        for r in rows:
            models = result.setdefault(r.user_id, [])
            if len(models) < 3:
                models.append(r.model)
        return result

    def get_model_breakdown_current_month(self, user_id: str) -> List[Dict]:
        month_start = _month_start_epoch()
        with get_db() as db:
            rows = (
                db.query(
                    UsageLedger.model,
                    func.coalesce(func.sum(UsageLedger.tokens_total), 0).label("tokens"),
                    func.coalesce(func.sum(UsageLedger.cost_eur), 0.0).label("cost_eur"),
                )
                .filter(
                    UsageLedger.user_id == user_id,
                    UsageLedger.observed_at >= month_start,
                )
                .group_by(UsageLedger.model)
                .order_by(func.coalesce(func.sum(UsageLedger.cost_eur), 0.0).desc())
                .all()
            )
        return [{"model": r.model, "tokens": int(r.tokens), "cost_eur": float(r.cost_eur)} for r in rows]

    def get_all_users_cost_current_month(self) -> Dict[str, float]:
        month_start = _month_start_epoch()
        with get_db() as db:
            rows = (
                db.query(UsageLedger.user_id, func.sum(UsageLedger.cost_eur))
                .filter(
                    UsageLedger.observed_at >= month_start,
                    UsageLedger.cost_eur.isnot(None),
                )
                .group_by(UsageLedger.user_id)
                .all()
            )
        return {uid: float(total or 0.0) for uid, total in rows}

    def get_models_used_current_month(self, user_id: str) -> List[str]:
        """Return top-3 distinct models by cost for the current month."""
        month_start = _month_start_epoch()
        with get_db() as db:
            rows = (
                db.query(UsageLedger.model, func.coalesce(func.sum(UsageLedger.cost_eur), 0.0).label("cost_eur"))
                .filter(
                    UsageLedger.user_id == user_id,
                    UsageLedger.observed_at >= month_start,
                )
                .group_by(UsageLedger.model)
                .order_by(func.coalesce(func.sum(UsageLedger.cost_eur), 0.0).desc())
                .limit(3)
                .all()
            )
        return [r.model for r in rows]

    def get_models_with_recent_priced_rows(self, model_names: List[str], since_ts: int) -> List[str]:
        """Return subset of model_names that have at least one priced row (cost_eur IS NOT NULL) since since_ts."""
        if not model_names:
            return []
        with get_db() as db:
            rows = (
                db.query(UsageLedger.model)
                .filter(
                    UsageLedger.model.in_(model_names),
                    UsageLedger.observed_at >= since_ts,
                    UsageLedger.cost_eur.isnot(None),
                )
                .distinct()
                .all()
            )
        return [r.model for r in rows]


UsageLedgerDB = UsageLedgerTable()
