import asyncio
import datetime
import logging
import os
import time
from typing import Set

log = logging.getLogger(__name__)

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

    rows = []
    # Model keys that came from the "neither field present" fallback below, not from a real
    # (if oddly-named) model/name value that happens to equal "unknown" - display-labeling
    # logic must check membership here, not string-match the key against "unknown" itself.
    models_missing_field: Set[str] = set()

    # Fetch rate once per sync batch — stable for up to 4h, consistent across all rows.
    batch_rate = get_eur_usd_rate()

    for obs in fetch_observations_since(since):
        obs_id = obs.get("id")
        if not obs_id:
            continue

        user_id = obs.get("userId") or ""
        model = obs.get("model") or obs.get("name") or "unknown"
        if not obs.get("model") and not obs.get("name"):
            models_missing_field.add(model)
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
        if cost_usd is not None and batch_rate is not None:
            eur_usd_rate = batch_rate
            cost_eur = cost_usd / batch_rate
        # else: rate unavailable, or no cost at all — cost_eur stays None

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

    newly_inserted_ids = UsageLedgerDB.bulk_insert_ignore(rows) if rows else set()
    inserted = len(newly_inserted_ids)

    # Deep rescan's cost backfill must run BEFORE classification, not after: its whole job is
    # fixing cost_eur on rows that are, by definition, duplicates from bulk_insert_ignore's
    # point of view (they already exist from an earlier poll) - so if classification only
    # looked at newly_inserted_ids, every row the rescan successfully backfills would be
    # invisible to it and a model recovering pricing only via the nightly rescan would never
    # trigger the "pricing recovered" alert.
    backfilled_cost_ids: Set[str] = set()
    updated_costs = 0
    updated_users = 0
    if deep_rescan and rows:
        backfilled_cost_ids = UsageLedgerDB.bulk_upsert_costs(rows)
        updated_costs = len(backfilled_cost_ids)
        if updated_costs:
            log.info("[ledger-poller] Deep rescan backfilled costs for %d previously-unpriced rows.", updated_costs)
        updated_users = UsageLedgerDB.bulk_upsert_user_ids(rows)
        if updated_users:
            log.info("[ledger-poller] Deep rescan backfilled user_id for %d previously-unattributed rows.", updated_users)

    # Classify unpriced/priced/recovered only from rows this poll actually inserted for the
    # first time, PLUS (deep rescan only) rows whose cost was just backfilled above - the same
    # observation can appear in several consecutive hot-path polls (the fetch window trails the
    # watermark by a couple of minutes for overlap-safety), and re-reading an already-synced
    # row must not re-trigger alert logic every single poll. A deep-rescan backfill is the one
    # case where a "duplicate" row legitimately carries new information (its price).
    #
    # "Unpriced" means the row has real usage but no cost - a row with no usage at all isn't
    # evidence the model lacks pricing, only that this particular observation had nothing to
    # price (see the "missing usage" investigation in project history). Conflating the two
    # mislabels models that are genuinely priced in Langfuse as unpriced, and alerts on them
    # every time a usage-less observation comes through.
    unpriced_models: Set[str] = set()
    priced_models: Set[str] = set()
    for r in rows:
        obs_id = r["langfuse_observation_id"]
        newly_priced_by_rescan = obs_id in backfilled_cost_ids
        if obs_id not in newly_inserted_ids and not newly_priced_by_rescan:
            continue
        if newly_priced_by_rescan:
            priced_models.add(r["model"])
            continue
        has_usage = bool(r["tokens_input"] or r["tokens_output"] or r["tokens_total"])
        if r["cost_eur"] is not None:
            priced_models.add(r["model"])
        elif has_usage:
            unpriced_models.add(r["model"])

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

    # Alert admin about models with no Langfuse pricing configured (aggregated). Dedup state
    # lives in billing_alert_state (not in-process memory) so a restart or a second concurrent
    # instance doesn't re-send an alert that's still within its cooldown, and a model flapping
    # between priced/unpriced across polls is naturally rate-limited to at most one alert per
    # BILLING_ALERT_COOLDOWN_SECONDS instead of re-arming on every relapse.
    from open_webui.models.billing_alert_state import (
        ALERT_TYPE_UNPRICED_MODEL,
        BillingAlertStateDB,
    )

    def _display_model_name(model: str) -> str:
        # Checks provenance (models_missing_field, populated above from the actual observation
        # fields), not model == "unknown" - a real observation can independently report its
        # own model/name as the literal string "unknown", and that must not be relabeled as a
        # missing-data case. Display-only either way; the underlying dedup/ledger key is
        # unchanged.
        return 'unknown (missing model field)' if model in models_missing_field else model

    # Claim first, send second: try_claim_alert() atomically reserves each model before the
    # email goes out, so two instances racing on the same due model can't both claim it (only
    # one INSERT/conditional-UPDATE wins). should_alert() alone would be a check-then-send
    # race - both instances could see "due" before either records anything.
    claimed_unpriced = {m for m in unpriced_models if BillingAlertStateDB.try_claim_alert(ALERT_TYPE_UNPRICED_MODEL, m)}
    if claimed_unpriced:
        try:
            from open_webui.utils.email import send_unpriced_models_email
            admin = asyncio.run(Users.get_super_admin_user())
            if admin and admin.email:
                sent = send_unpriced_models_email(
                    to=admin.email,
                    model_names=[_display_model_name(m) for m in sorted(claimed_unpriced)],
                )
                if sent:
                    for model in claimed_unpriced:
                        BillingAlertStateDB.confirm_alert(ALERT_TYPE_UNPRICED_MODEL, model)
                    log.warning("[ledger-poller] Alerted admin about unpriced models: %s", claimed_unpriced)
                else:
                    # Claim already recorded even though the send failed - release it so the
                    # next poll can retry immediately instead of waiting out the cooldown.
                    for model in claimed_unpriced:
                        BillingAlertStateDB.release_claim(ALERT_TYPE_UNPRICED_MODEL, model)
                    log.error("[ledger-poller] Failed to send unpriced-model alert (will retry next poll)")
            else:
                for model in claimed_unpriced:
                    BillingAlertStateDB.release_claim(ALERT_TYPE_UNPRICED_MODEL, model)
        except Exception as exc:
            for model in claimed_unpriced:
                BillingAlertStateDB.release_claim(ALERT_TYPE_UNPRICED_MODEL, model)
            log.error("[ledger-poller] Failed to send unpriced-model alert: %s", exc)

    # Alert admin when a previously unpriced model starts producing priced observations.
    # Check both: models in the current sync window (priced_models) AND models that may
    # have been priced in the ledger recently but haven't appeared in this sync window
    # (e.g. rarely-used models). Use a 24h lookback in the ledger as the broader check.
    alerted_models = BillingAlertStateDB.get_alerted_keys(ALERT_TYPE_UNPRICED_MODEL)
    ledger_recovered: set[str] = set()
    if alerted_models:
        since_24h = int(time.time()) - 86400
        ledger_recovered = set(UsageLedgerDB.get_models_with_recent_priced_rows(
            list(alerted_models), since_24h
        ))
    # A model with a fresh unpriced observation THIS poll must never also be claimed as
    # recovered in it - sending both "unpriced" and "restored" for one model in one poll is a
    # direct contradiction (observed as alert spam when a batch mixed a priced and an unpriced
    # observation for the same model). Excluding unpriced_models, not just claimed_unpriced,
    # matters: a model still inside its alert cooldown loses the
    # try_claim_alert race (so it never reaches claimed_unpriced) even though this poll saw a
    # real unpriced observation for it - excluding only claimed_unpriced would let a stale
    # priced row (from ledger_recovered's 24h lookback, or an older row this poll's deep-rescan
    # backfilled) clear that model's alert state in the very same poll its fresh unpriced
    # evidence was seen.
    candidate_recovered = ((priced_models | ledger_recovered) & alerted_models) - unpriced_models
    # Claim first, send second - same reasoning as the unpriced-model block above: computing
    # candidate_recovered and sending the email are separate from the DB write, so without an
    # atomic claim two instances could both compute the same set and both email before either
    # records the transition. try_claim_recovery() returns the model's prior last_alerted_at
    # on a win (None on a loss) so a failed send can fully restore it via
    # release_recovery_claim() - not just status, or the failed claim's timestamp bump would
    # silently extend this model's unpriced-model cooldown despite no email ever going out.
    claimed_recovered: dict[str, int] = {}
    for m in candidate_recovered:
        previous_last_alerted_at = BillingAlertStateDB.try_claim_recovery(ALERT_TYPE_UNPRICED_MODEL, m)
        if previous_last_alerted_at is not None:
            claimed_recovered[m] = previous_last_alerted_at

    if claimed_recovered:
        try:
            from open_webui.utils.email import send_model_pricing_recovered_email
            admin = asyncio.run(Users.get_super_admin_user())
            if admin and admin.email:
                sent = send_model_pricing_recovered_email(
                    to=admin.email,
                    model_names=[_display_model_name(m) for m in sorted(claimed_recovered)],
                )
                if sent:
                    for model in claimed_recovered:
                        BillingAlertStateDB.confirm_recovery(ALERT_TYPE_UNPRICED_MODEL, model)
                    log.info("[ledger-poller] Alerted admin about recovered model pricing: %s", set(claimed_recovered))
                else:
                    for model, previous_last_alerted_at in claimed_recovered.items():
                        BillingAlertStateDB.release_recovery_claim(
                            ALERT_TYPE_UNPRICED_MODEL, model, previous_last_alerted_at
                        )
                    log.error("[ledger-poller] Failed to send pricing-recovered alert (will retry next poll)")
            else:
                for model, previous_last_alerted_at in claimed_recovered.items():
                    BillingAlertStateDB.release_recovery_claim(
                        ALERT_TYPE_UNPRICED_MODEL, model, previous_last_alerted_at
                    )
        except Exception as exc:
            for model, previous_last_alerted_at in claimed_recovered.items():
                BillingAlertStateDB.release_recovery_claim(
                    ALERT_TYPE_UNPRICED_MODEL, model, previous_last_alerted_at
                )
            log.error("[ledger-poller] Failed to send model pricing recovered alert: %s", exc)

    # Alert admin if ECB has been unreachable since startup
    uptime = time.time() - _poller_started_at
    if uptime > 600:  # 10 minutes
        import open_webui.langfuse.ecb_rates as _ecb_module
        from open_webui.models.billing_alert_state import (
            ALERT_TYPE_ECB_UNREACHABLE,
            ECB_ALERT_KEY,
            BillingAlertStateDB,
        )
        if (
            _ecb_module._last_known_rate is None
            and BillingAlertStateDB.try_claim_alert(ALERT_TYPE_ECB_UNREACHABLE, ECB_ALERT_KEY)
        ):
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
                        BillingAlertStateDB.confirm_alert(ALERT_TYPE_ECB_UNREACHABLE, ECB_ALERT_KEY)
                        log.error("[ledger-poller] Sent ECB unreachable alert to admin.")
                    else:
                        BillingAlertStateDB.release_claim(ALERT_TYPE_ECB_UNREACHABLE, ECB_ALERT_KEY)
                        log.error("[ledger-poller] Failed to send ECB alert (will retry next poll).")
                else:
                    BillingAlertStateDB.release_claim(ALERT_TYPE_ECB_UNREACHABLE, ECB_ALERT_KEY)
            except Exception as exc:
                BillingAlertStateDB.release_claim(ALERT_TYPE_ECB_UNREACHABLE, ECB_ALERT_KEY)
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
