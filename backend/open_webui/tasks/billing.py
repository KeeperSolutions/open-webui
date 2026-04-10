import asyncio
import datetime
import logging

log = logging.getLogger(__name__)


async def _run_usage_report():
    """Push current-month Langfuse costs to Stripe for all active subscribers."""
    from open_webui.env import BILLING_ENABLED, STRIPE_SECRET_KEY
    from open_webui.models.billing import StripeBillings
    from open_webui.models.users import Users

    if not BILLING_ENABLED or not STRIPE_SECRET_KEY:
        return

    import stripe

    stripe.api_key = STRIPE_SECRET_KEY

    # Fetch Langfuse current-month costs (runs in thread — it's a blocking HTTP call)
    try:
        loop = asyncio.get_event_loop()
        from open_webui.langfuse.metrics import get_current_month

        rows = await loop.run_in_executor(None, get_current_month)
        # Build a lookup: email → cost in euros
        cost_by_email: dict[str, float] = {}
        for r in rows:
            email = r.get("user", "")
            if email:
                cost_by_email[email] = cost_by_email.get(email, 0.0) + r.get("cost", 0.0)
    except Exception as e:
        log.error(f"[billing-reporter] Failed to fetch Langfuse metrics: {e}")
        return

    active_records = StripeBillings.get_all_active()
    if not active_records:
        log.info("[billing-reporter] No active billing records, skipping.")
        return

    log.info(f"[billing-reporter] Reporting usage for {len(active_records)} subscribers.")

    for record in active_records:
        if not record.stripe_subscription_item_id:
            log.warning(f"[billing-reporter] user_id={record.user_id} has no subscription item ID, skipping.")
            continue

        # Resolve email from user_id
        try:
            user = Users.get_user_by_id(record.user_id)
            if not user:
                log.warning(f"[billing-reporter] user_id={record.user_id} not found, skipping.")
                continue
            email = user.email
        except Exception as e:
            log.warning(f"[billing-reporter] Could not resolve user_id={record.user_id}: {e}")
            continue

        cost_eur = cost_by_email.get(email, 0.0)
        # Stripe quantity = integer euro cents
        quantity = max(0, int(round(cost_eur * 100)))

        try:
            stripe.SubscriptionItem.create_usage_record(
                record.stripe_subscription_item_id,
                quantity=quantity,
                timestamp="now",
                action="set",  # snapshot — safe to run multiple times per day
            )
            log.info(
                f"[billing-reporter] Reported {quantity} cents (€{cost_eur:.4f}) "
                f"for {email} (sub_item={record.stripe_subscription_item_id})"
            )
        except Exception as e:
            log.error(
                f"[billing-reporter] Failed to report usage for {email}: {e}"
            )
            # Continue — don't abort the entire job for one user


def _seconds_until_next_midnight_utc() -> float:
    """Returns seconds until the next UTC midnight."""
    now = datetime.datetime.utcnow()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (tomorrow - now).total_seconds()


async def periodic_billing_usage_reporter():
    """
    Async task that wakes up daily at UTC midnight and pushes usage to Stripe.
    Designed to be launched with asyncio.create_task() at app startup.
    """
    log.info("[billing-reporter] Task started.")

    while True:
        wait = _seconds_until_next_midnight_utc()
        log.debug(f"[billing-reporter] Next run in {wait:.0f}s ({wait/3600:.1f}h).")
        await asyncio.sleep(wait)

        log.info("[billing-reporter] Running daily usage report.")
        try:
            await _run_usage_report()
        except Exception as e:
            log.error(f"[billing-reporter] Unexpected error: {e}")
        # Small buffer to avoid drift from midnight into the same second
        await asyncio.sleep(5)
