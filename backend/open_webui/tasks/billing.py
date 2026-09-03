import asyncio
import datetime
import logging
import os
import time
from typing import Set

log = logging.getLogger(__name__)

# Alert state — reset on process restart
_ecb_alert_sent: bool = False
_unpriced_models_alerted: Set[str] = set()   # models we've sent an unpriced alert for
_priced_models_recovered: Set[str] = set()   # subset of above that have since been priced
_poller_started_at: float = 0.0


def _seconds_until_next_midnight_utc() -> float:
    """Returns seconds until the next UTC midnight."""
    now = datetime.datetime.utcnow()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (tomorrow - now).total_seconds()


async def periodic_nightly_deep_rescan():
    """
    Async task that wakes daily at UTC midnight and runs a 7-day deep rescan of
    Langfuse observations to backfill cost_eur for previously-unpriced models.
    Designed to be launched with asyncio.create_task() at app startup.
    """
    from open_webui.env import BILLING_ENABLED
    if not BILLING_ENABLED:
        return

    log.info("[nightly-rescan] Task started.")

    while True:
        wait = _seconds_until_next_midnight_utc()
        log.debug(f"[nightly-rescan] Next run in {wait:.0f}s ({wait/3600:.1f}h).")
        await asyncio.sleep(wait)

        try:
            loop = asyncio.get_running_loop()
            rescan_since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
            log.info("[nightly-rescan] Starting deep rescan from %s.", rescan_since.isoformat())
            await loop.run_in_executor(None, lambda: _sync_observations(rescan_since, deep_rescan=True))
        except Exception as e:
            log.error(f"[nightly-rescan] Deep rescan failed: {e}")

        # Small buffer to avoid drift from midnight into the same second
        await asyncio.sleep(5)


def _sync_observations(since: datetime.datetime, *, deep_rescan: bool = False) -> int:
    """Blocking: fetch Langfuse observations since `since`, convert to EUR, insert into ledger.

    When deep_rescan=True (nightly path), rows that already exist with cost_eur=NULL are
    updated if Langfuse now has pricing for them (bulk_upsert_costs). The hot-path (every
    5 min) uses insert-ignore only — fast and cheap.
    """
    from open_webui.langfuse.observations import fetch_observations_since
    from open_webui.langfuse.ecb_rates import get_eur_usd_rate
    from open_webui.models.usage_ledger import UsageLedgerDB
    from open_webui.models.users import Users
    global _ecb_alert_sent, _unpriced_models_alerted, _priced_models_recovered, _poller_started_at

    rows = []
    unpriced_models: Set[str] = set()
    priced_models: Set[str] = set()

    # Fetch rate once per sync batch — stable for up to 4h, consistent across all rows.
    batch_rate = get_eur_usd_rate()

    for obs in fetch_observations_since(since):
        obs_id = obs.get("id")
        if not obs_id:
            continue

        user_id = obs.get("userId") or ""
        model = obs.get("model") or obs.get("name") or "unknown"
        usage = obs.get("usage") or {}
        tokens_input = int(usage.get("input") or 0)
        tokens_output = int(usage.get("output") or 0)
        tokens_total = int(usage.get("total") or 0)

        # Parse observed_at from startTime
        start_time = obs.get("startTime", "")
        try:
            ts = start_time.rstrip("Z").split(".")[0]
            observed_at = int(
                datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
                .replace(tzinfo=datetime.timezone.utc)
                .timestamp()
            )
        except Exception:
            observed_at = int(time.time())

        cost_usd = obs.get("calculatedTotalCost")
        if cost_usd is not None:
            try:
                cost_usd = float(cost_usd)
            except (TypeError, ValueError):
                cost_usd = None

        eur_usd_rate = None
        cost_eur = None
        if cost_usd is not None:
            if batch_rate is not None:
                eur_usd_rate = batch_rate
                cost_eur = cost_usd / batch_rate
                priced_models.add(model)
            # else: rate unavailable — cost_eur stays None; don't mark as priced
        else:
            unpriced_models.add(model)

        rows.append({
            "langfuse_observation_id": obs_id,
            "user_id": user_id,
            "model": model,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "tokens_total": tokens_total,
            "cost_usd": cost_usd,
            "eur_usd_rate": eur_usd_rate,
            "cost_eur": cost_eur,
            "observed_at": observed_at,
        })

    inserted = UsageLedgerDB.bulk_insert_ignore(rows) if rows else 0

    updated_costs = 0
    updated_users = 0
    if deep_rescan and rows:
        updated_costs = UsageLedgerDB.bulk_upsert_costs(rows)
        if updated_costs:
            log.info("[ledger-poller] Deep rescan backfilled costs for %d previously-unpriced rows.", updated_costs)
        updated_users = UsageLedgerDB.bulk_upsert_user_ids(rows)
        if updated_users:
            log.info("[ledger-poller] Deep rescan backfilled user_id for %d previously-unattributed rows.", updated_users)

    log.info("[ledger-poller] Synced %d observations (%d inserted, %d cost-backfilled, %d user-backfilled).", len(rows), inserted, updated_costs, updated_users)

    # Log overage for credits users — signals potential abuse if repeated across syncs
    try:
        from open_webui.models.user_credits import UserCreditsDB, eur_to_credits
        distinct_users = list({r["user_id"] for r in rows if r.get("user_id")})
        cost_by_user = UsageLedgerDB.get_cost_eur_for_users_current_month(distinct_users)
        for uid in distinct_users:
            row_creds = UserCreditsDB.get(uid)
            if row_creds and row_creds.balance > 0:
                cost = cost_by_user.get(uid, 0.0)
                used = eur_to_credits(cost, row_creds.credits_per_eur_cent)
                remaining = row_creds.balance - used
                if remaining < 0:
                    log.warning(
                        "[ledger-poller] credits overage: user=%s overshot=%d credits (balance=%d used=%d)",
                        uid, abs(remaining), row_creds.balance, used,
                    )
    except Exception as _e:
        log.debug("[ledger-poller] overage check skipped: %s", _e)

    # Re-arm alert state for models that were "recovered" but have lost pricing again
    relapsed = unpriced_models & _priced_models_recovered
    if relapsed:
        _priced_models_recovered -= relapsed
        _unpriced_models_alerted -= relapsed
        log.warning("[ledger-poller] Models lost pricing again, re-arming alerts: %s", relapsed)

    # Alert admin about models with no Langfuse pricing configured (aggregated)
    new_unpriced = unpriced_models - _unpriced_models_alerted
    if new_unpriced:
        try:
            from open_webui.utils.email import send_unpriced_models_email
            admin = asyncio.run(Users.get_super_admin_user())
            if admin and admin.email:
                sent = send_unpriced_models_email(to=admin.email, model_names=sorted(new_unpriced))
                if sent:
                    _unpriced_models_alerted.update(new_unpriced)
                    log.warning("[ledger-poller] Alerted admin about unpriced models: %s", new_unpriced)
                else:
                    log.error("[ledger-poller] Failed to send unpriced-model alert (will retry next poll)")
        except Exception as exc:
            log.error("[ledger-poller] Failed to send unpriced-model alert: %s", exc)

    # Alert admin when a previously unpriced model starts producing priced observations.
    # Check both: models in the current sync window (priced_models) AND models that may
    # have been priced in the ledger recently but haven't appeared in this sync window
    # (e.g. rarely-used models). Use a 24h lookback in the ledger as the broader check.
    unalerted_models = _unpriced_models_alerted - _priced_models_recovered
    ledger_recovered: set[str] = set()
    if unalerted_models:
        since_24h = int(time.time()) - 86400
        ledger_recovered = set(UsageLedgerDB.get_models_with_recent_priced_rows(
            list(unalerted_models), since_24h
        ))
    newly_recovered = ((priced_models | ledger_recovered) & _unpriced_models_alerted) - _priced_models_recovered
    if newly_recovered:
        try:
            from open_webui.utils.email import send_model_pricing_recovered_email
            admin = asyncio.run(Users.get_super_admin_user())
            if admin and admin.email:
                sent = send_model_pricing_recovered_email(to=admin.email, model_names=sorted(newly_recovered))
                if sent:
                    _priced_models_recovered.update(newly_recovered)
                    log.info("[ledger-poller] Alerted admin about recovered model pricing: %s", newly_recovered)
                else:
                    log.error("[ledger-poller] Failed to send pricing-recovered alert (will retry next poll)")
        except Exception as exc:
            log.error("[ledger-poller] Failed to send model pricing recovered alert: %s", exc)

    # Alert admin if ECB has been unreachable since startup
    if not _ecb_alert_sent:
        uptime = time.time() - _poller_started_at
        if uptime > 600:  # 10 minutes
            import open_webui.langfuse.ecb_rates as _ecb_module
            if _ecb_module._last_known_rate is None:
                try:
                    from open_webui.models.users import Users
                    from open_webui.utils.email import send_ecb_unreachable_email

                    admin = asyncio.run(Users.get_super_admin_user())
                    if admin and admin.email:
                        startup_time = datetime.datetime.fromtimestamp(
                            _poller_started_at, tz=datetime.timezone.utc
                        ).strftime("%Y-%m-%d %H:%M:%S UTC")
                        error_detail = _ecb_module._last_error or "unknown error"
                        sent = send_ecb_unreachable_email(
                            to=admin.email,
                            startup_time=startup_time,
                            error_detail=error_detail,
                        )
                        if sent:
                            _ecb_alert_sent = True
                            log.error("[ledger-poller] Sent ECB unreachable alert to admin.")
                        else:
                            log.error("[ledger-poller] Failed to send ECB alert (will retry next poll).")
                except Exception as exc:
                    log.error("[ledger-poller] Failed to send ECB alert: %s", exc)

    return inserted


async def periodic_ledger_poller():
    """Async task that syncs Langfuse observations into usage_ledger every 5 minutes."""
    from open_webui.env import BILLING_ENABLED

    if not BILLING_ENABLED:
        return

    global _poller_started_at
    _poller_started_at = time.time()
    log.info("[ledger-poller] Task started.")

    watermark: datetime.datetime | None = None

    while True:
        try:
            from open_webui.env import LEDGER_BOOTSTRAP_DAYS
            from open_webui.models.usage_ledger import UsageLedgerDB

            loop = asyncio.get_running_loop()

            if watermark is None:
                max_ts = UsageLedgerDB.get_max_observed_at()
                if max_ts is not None:
                    watermark = datetime.datetime.fromtimestamp(max_ts, tz=datetime.timezone.utc)
                else:
                    watermark = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                        days=LEDGER_BOOTSTRAP_DAYS
                    )
                log.info("[ledger-poller] Watermark initialised: %s", watermark.isoformat())

            # 2-minute overlap to catch late-arriving Langfuse writes
            since = watermark - datetime.timedelta(minutes=2)
            await loop.run_in_executor(None, _sync_observations, since)

            # Reload watermark from DB after sync
            max_ts = UsageLedgerDB.get_max_observed_at()
            if max_ts is not None:
                watermark = datetime.datetime.fromtimestamp(max_ts, tz=datetime.timezone.utc)

        except Exception as exc:
            log.error("[ledger-poller] Unexpected error: %s", exc)

        try:
            poll_interval = int(os.environ.get("LEDGER_POLL_INTERVAL", "300"))
        except (ValueError, TypeError):
            poll_interval = 300
        await asyncio.sleep(poll_interval)
