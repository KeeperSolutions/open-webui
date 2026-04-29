import datetime
import logging
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.env import (
    BILLING_ENABLED,
    INTERNAL_EMAIL_DOMAINS,
    STRIPE_FREE_TIER_CENTS,
    STRIPE_PRICE_ID,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    TRIAL_CREDIT_EUR,
)
from open_webui.models.billing import StripeBillings
from open_webui.utils.auth import get_admin_user, get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()


# ---------- Helpers ----------


def is_internal_user(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    return domain in INTERNAL_EMAIL_DOMAINS


def require_billing_enabled():
    if not BILLING_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not enabled on this instance.",
        )


def get_stripe_client() -> stripe.StripeClient:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe is not configured.",
        )
    return stripe.StripeClient(STRIPE_SECRET_KEY)


def _get_user_alltime_cost(email: str, created_at: int) -> float:
    """Return total Langfuse cost (EUR) for `email` since account creation."""
    try:
        from open_webui.langfuse.metrics import get_alltime_since

        # Use the earlier of: billing record created_at or start of current year
        # This ensures we don't miss usage that predates the billing record
        year_start = datetime.datetime(datetime.datetime.utcnow().year, 1, 1)
        record_start = datetime.datetime.utcfromtimestamp(created_at)
        since = min(year_start, record_start)

        rows = get_alltime_since(since)
        return sum(r["cost"] for r in rows if r.get("user") == email)
    except Exception as e:
        log.warning(f"Could not fetch alltime Langfuse cost for {email}: {e}")
        return 0.0


def _get_user_current_month_cost(email: str) -> float:
    try:
        from open_webui.langfuse.metrics import get_current_month

        rows = get_current_month()
        return sum(r["cost"] for r in rows if r.get("user") == email)
    except Exception as e:
        log.warning(f"Could not fetch monthly Langfuse cost for {email}: {e}")
        return 0.0


async def check_billing_access(user=Depends(get_verified_user)):
    """
    Dependency that blocks users from making AI completions based on billing state.
    - Admins: always allowed
    - Internal users (@keepersolutions.com etc.): always allowed
    - Trial users: allowed while credit_used < TRIAL_CREDIT_EUR
    - Paid users: allowed while subscription is active
    """
    if not BILLING_ENABLED:
        return user

    import os
    billing_test_mode = os.environ.get("BILLING_TEST_MODE", "false").lower() == "true"
    if user.role == "admin" and not billing_test_mode:
        return user
    if is_internal_user(user.email) and not billing_test_mode:
        return user

    record = StripeBillings.get_by_user_id(user.id)
    if record is None:
        # No billing record yet — allow (will be created on next explicit action)
        return user

    if record.plan_tier == "paid":
        if record.subscription_status in ("past_due",):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Your payment is past due. Please update your billing details at /billing.",
            )
        if record.subscription_status == "canceled":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Your subscription has been canceled. Please visit /billing to reactivate.",
            )
        return user

    if record.plan_tier == "trial":
        cost_used = _get_user_alltime_cost(user.email, record.created_at)
        if cost_used >= TRIAL_CREDIT_EUR:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Your €{TRIAL_CREDIT_EUR:.2f} trial credit has been used up. "
                    "Please upgrade your plan at /billing."
                ),
            )
        return user

    # plan_tier is None or "internal" — allow (covers legacy rows and internal users)
    return user


# ---------- Auto-onboard (called from auths.py on signup) ----------


async def auto_onboard_user(user, request=None):
    """
    Called after a new user signs up.
    - Internal users get plan_tier='internal' (no Stripe customer).
    - External users get a Stripe Customer + €2 trial credit + plan_tier='trial'.
    """
    if not BILLING_ENABLED:
        return

    existing = StripeBillings.get_by_user_id(user.id)
    if existing is not None:
        return  # Already onboarded

    if is_internal_user(user.email):
        StripeBillings.upsert(
            user_id=user.id,
            plan_tier="internal",
        )
        log.info(f"[billing] Internal user onboarded: {user.email}")
        return

    # External user — create Stripe Customer
    if not STRIPE_SECRET_KEY:
        log.warning("[billing] STRIPE_SECRET_KEY not set; skipping external user onboarding.")
        return

    try:
        client = get_stripe_client()
        customer = client.v1.customers.create(
            params={
                "email": user.email,
                "name": user.name,
                "metadata": {"user_id": user.id},
            }
        )
        customer_id = customer.id

        # Apply trial credit
        free_tier_applied = False
        if STRIPE_FREE_TIER_CENTS > 0:
            try:
                client.v1.customers.balance_transactions.create(
                    customer_id,
                    params={
                        "amount": -STRIPE_FREE_TIER_CENTS,
                        "currency": "eur",
                        "description": f"Trial credit (€{STRIPE_FREE_TIER_CENTS / 100:.2f})",
                    },
                )
                free_tier_applied = True
            except stripe.StripeError as e:
                log.warning(f"[billing] Could not apply trial credit for {user.email}: {e}")

        StripeBillings.upsert(
            user_id=user.id,
            stripe_customer_id=customer_id,
            plan_tier="trial",
            free_tier_credit_applied=free_tier_applied,
        )
        log.info(f"[billing] External user onboarded as trial: {user.email} (customer={customer_id})")

    except stripe.StripeError as e:
        log.error(f"[billing] Failed to onboard external user {user.email}: {e}")


# ---------- Response models ----------


class BillingStatusResponse(BaseModel):
    enabled: bool
    plan_tier: Optional[str] = None  # internal | trial | paid | None

    # Trial fields
    credit_limit_eur: float = 0.0
    credit_used_eur: float = 0.0
    credit_remaining_eur: float = 0.0

    # Paid subscription fields
    subscription_status: Optional[str] = None
    upcoming_invoice_eur: Optional[float] = None

    # Current month usage (all tiers)
    current_month_cost_eur: float = 0.0

    is_configured: bool = False


class CheckoutResponse(BaseModel):
    url: str


class PortalResponse(BaseModel):
    url: str


# ---------- Endpoints ----------


@router.get("/status", response_model=BillingStatusResponse)
async def get_billing_status(user=Depends(get_verified_user)):
    require_billing_enabled()

    record = StripeBillings.get_by_user_id(user.id)
    current_month_cost = _get_user_current_month_cost(user.email)

    if not record:
        return BillingStatusResponse(
            enabled=True,
            is_configured=False,
            current_month_cost_eur=current_month_cost,
        )

    if record.plan_tier == "internal":
        return BillingStatusResponse(
            enabled=True,
            plan_tier="internal",
            is_configured=True,
            current_month_cost_eur=current_month_cost,
        )

    if record.plan_tier == "trial":
        cost_used = _get_user_alltime_cost(user.email, record.created_at)
        credit_limit = TRIAL_CREDIT_EUR
        remaining = max(0.0, credit_limit - cost_used)
        return BillingStatusResponse(
            enabled=True,
            plan_tier="trial",
            is_configured=True,
            credit_limit_eur=credit_limit,
            credit_used_eur=round(cost_used, 4),
            credit_remaining_eur=round(remaining, 4),
            current_month_cost_eur=current_month_cost,
        )

    if record.plan_tier == "paid":
        if not record.stripe_subscription_id:
            return BillingStatusResponse(
                enabled=True,
                plan_tier="paid",
                is_configured=False,
                current_month_cost_eur=current_month_cost,
            )

        client = get_stripe_client()

        try:
            sub = client.v1.subscriptions.retrieve(record.stripe_subscription_id)
            sub_status = sub.status
        except stripe.StripeError as e:
            log.error(f"Stripe subscription retrieve error: {e}")
            sub_status = record.subscription_status

        upcoming_eur: Optional[float] = None
        try:
            invoice = client.v1.invoices.create_preview(
                params={"customer": record.stripe_customer_id}
            )
            upcoming_eur = invoice.amount_due / 100
        except stripe.StripeError:
            pass

        return BillingStatusResponse(
            enabled=True,
            plan_tier="paid",
            is_configured=True,
            subscription_status=sub_status,
            upcoming_invoice_eur=upcoming_eur,
            current_month_cost_eur=current_month_cost,
        )

    # Fallback for legacy rows without plan_tier — treat as internal
    return BillingStatusResponse(
        enabled=True,
        plan_tier="internal",
        is_configured=True,
        current_month_cost_eur=current_month_cost,
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout_session(request: Request, user=Depends(get_verified_user)):
    """Create a Stripe Checkout Session for the €45/month flat subscription."""
    require_billing_enabled()

    if not STRIPE_PRICE_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STRIPE_PRICE_ID is not configured.",
        )

    record = StripeBillings.get_by_user_id(user.id)

    if record and record.plan_tier == "internal":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Internal users do not need a subscription.",
        )

    client = get_stripe_client()

    # Reuse existing Stripe customer if available
    customer_id = record.stripe_customer_id if record else None

    if not customer_id:
        try:
            customer = client.v1.customers.create(
                params={
                    "email": user.email,
                    "name": user.name,
                    "metadata": {"user_id": user.id},
                }
            )
            customer_id = customer.id
        except stripe.StripeError as e:
            log.error(f"Stripe customer create error: {e}")
            raise HTTPException(status_code=502, detail="Failed to create Stripe customer.")

    webui_url = request.app.state.config.WEBUI_URL or str(request.base_url).rstrip("/")

    try:
        session = client.v1.checkout.sessions.create(
            params={
                "customer": customer_id,
                "mode": "subscription",
                "line_items": [{"price": STRIPE_PRICE_ID, "quantity": 1}],
                "success_url": f"{webui_url}/billing?checkout=success",
                "cancel_url": f"{webui_url}/billing?checkout=canceled",
                "metadata": {"user_id": user.id},
            }
        )
    except stripe.StripeError as e:
        log.error(f"Stripe checkout session create error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create checkout session.")

    # Store checkout session ID so webhook can match it
    StripeBillings.upsert(
        user_id=user.id,
        stripe_customer_id=customer_id,
        plan_tier=record.plan_tier if record else "trial",
        checkout_session_id=session.id,
        free_tier_credit_applied=record.free_tier_credit_applied if record else False,
    )

    return CheckoutResponse(url=session.url)


@router.post("/portal", response_model=PortalResponse)
async def billing_portal(request: Request, user=Depends(get_verified_user)):
    require_billing_enabled()
    client = get_stripe_client()

    record = StripeBillings.get_by_user_id(user.id)
    if not record or not record.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account found.",
        )

    webui_url = request.app.state.config.WEBUI_URL or str(request.base_url).rstrip("/")
    try:
        session = client.v1.billing_portal.sessions.create(
            params={
                "customer": record.stripe_customer_id,
                "return_url": f"{webui_url}/billing",
            }
        )
    except stripe.StripeError as e:
        log.error(f"Stripe portal session error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create billing portal session.")

    return PortalResponse(url=session.url)


@router.get("/invoices")
async def get_invoices(user=Depends(get_verified_user)):
    require_billing_enabled()
    client = get_stripe_client()

    record = StripeBillings.get_by_user_id(user.id)
    if not record or not record.stripe_customer_id:
        return []

    try:
        invoices = client.v1.invoices.list(
            params={"customer": record.stripe_customer_id, "limit": 24}
        )
    except stripe.StripeError as e:
        log.error(f"Stripe invoices list error: {e}")
        return []

    return [
        {
            "id": inv.id,
            "date": inv.created,
            "amount_eur": inv.amount_due / 100,
            "status": inv.status,
            "pdf_url": inv.invoice_pdf,
            "hosted_url": inv.hosted_invoice_url,
        }
        for inv in invoices.data
    ]


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=400, detail="Webhook secret not configured.")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.")

    event_type = event.type
    data = event.data.object

    if event_type == "checkout.session.completed":
        customer_id = getattr(data, "customer", None)
        subscription_id = getattr(data, "subscription", None)

        if customer_id:
            record = StripeBillings.get_by_customer_id(customer_id)
            if record:
                StripeBillings.upsert(
                    user_id=record.user_id,
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    plan_tier="paid",
                    subscription_status="active",
                    free_tier_credit_applied=record.free_tier_credit_applied,
                )
                log.info(f"[billing] Checkout completed: user_id={record.user_id} → paid plan")

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = getattr(data, "customer", None)
        new_status = getattr(data, "status", None)
        subscription_id = getattr(data, "id", None)
        if customer_id and new_status:
            StripeBillings.update_subscription_status(customer_id, new_status)
            log.info(f"[billing] Subscription updated: customer={customer_id} status={new_status}")

    elif event_type == "invoice.payment_failed":
        customer_id = getattr(data, "customer", None)
        if customer_id:
            StripeBillings.update_subscription_status(customer_id, "past_due")
            log.warning(f"[billing] Payment failed: customer={customer_id}")

    elif event_type == "invoice.paid":
        customer_id = getattr(data, "customer", None)
        if customer_id:
            record = StripeBillings.get_by_customer_id(customer_id)
            if record and record.subscription_status == "past_due":
                StripeBillings.update_subscription_status(customer_id, "active")
                log.info(f"[billing] Payment recovered: customer={customer_id}")

    elif event_type == "payment_method.attached":
        customer_id = getattr(data, "customer", None)
        pm_id = getattr(data, "id", None)
        if customer_id and pm_id:
            record = StripeBillings.get_by_customer_id(customer_id)
            if record:
                StripeBillings.upsert(
                    user_id=record.user_id,
                    stripe_payment_method_id=pm_id,
                    free_tier_credit_applied=record.free_tier_credit_applied,
                )

    return {"received": True}


@router.get("/admin/summary")
async def admin_billing_summary(user=Depends(get_admin_user)):
    require_billing_enabled()

    from open_webui.models.users import Users

    all_records = StripeBillings.get_all()
    billing_by_user_id = {r.user_id: r for r in all_records}

    cost_by_email: dict[str, float] = {}
    try:
        from open_webui.langfuse.metrics import get_current_month

        rows = get_current_month()
        for r in rows:
            email = r.get("user", "")
            if email:
                cost_by_email[email] = cost_by_email.get(email, 0.0) + r.get("cost", 0.0)
    except Exception as e:
        log.warning(f"Admin summary: could not fetch Langfuse metrics: {e}")

    result = []
    for record in all_records:
        u = Users.get_user_by_id(record.user_id)
        if not u:
            continue
        result.append(
            {
                "user_id": u.id,
                "name": u.name,
                "email": u.email,
                "plan_tier": record.plan_tier,
                "subscription_status": record.subscription_status,
                "stripe_customer_id": record.stripe_customer_id,
                "current_month_cost_eur": round(cost_by_email.get(u.email, 0.0), 4),
                "free_tier_credit_applied": record.free_tier_credit_applied,
            }
        )

    all_user_ids_with_billing = {r.user_id for r in all_records}
    for u in Users.get_users()["users"]:
        if u.id not in all_user_ids_with_billing:
            result.append(
                {
                    "user_id": u.id,
                    "name": u.name,
                    "email": u.email,
                    "plan_tier": None,
                    "subscription_status": None,
                    "stripe_customer_id": None,
                    "current_month_cost_eur": round(cost_by_email.get(u.email, 0.0), 4),
                    "free_tier_credit_applied": False,
                }
            )

    result.sort(key=lambda x: x["current_month_cost_eur"], reverse=True)
    return result
