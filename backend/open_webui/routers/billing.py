import logging
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.env import (
    BILLING_ENABLED,
    BILLING_GRACE_PERIOD_DAYS,
    STRIPE_FREE_TIER_CENTS,
    STRIPE_PRICE_ID,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from open_webui.models.billing import StripeBillings
from open_webui.utils.auth import get_admin_user, get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()


def require_billing_enabled():
    if not BILLING_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not enabled on this instance.",
        )


async def check_billing_access(user=Depends(get_verified_user)):
    """
    Dependency that blocks past-due users from making AI completions.
    Admins always bypass. Only active when BILLING_ENABLED=True.
    """
    if not BILLING_ENABLED:
        return user
    if user.role == "admin":
        return user

    record = StripeBillings.get_by_user_id(user.id)
    if record is None:
        # No billing record yet — allow during onboarding grace period
        return user

    if record.subscription_status == "past_due":
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


def get_stripe_client() -> stripe.StripeClient:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe is not configured.",
        )
    return stripe.StripeClient(STRIPE_SECRET_KEY)


# ---------- Response models ----------


class BillingStatusResponse(BaseModel):
    enabled: bool
    subscription_status: Optional[str] = None
    current_month_cost_eur: float = 0.0
    upcoming_invoice_eur: Optional[float] = None
    has_payment_method: bool = False
    is_configured: bool = False


class SetupResponse(BaseModel):
    is_configured: bool
    subscription_status: Optional[str] = None


class PortalResponse(BaseModel):
    url: str


# ---------- Endpoints ----------


@router.get("/status", response_model=BillingStatusResponse)
async def get_billing_status(user=Depends(get_verified_user)):
    require_billing_enabled()

    record = StripeBillings.get_by_user_id(user.id)
    if not record or not record.stripe_subscription_id:
        return BillingStatusResponse(enabled=True, is_configured=False)

    client = get_stripe_client()

    # Fetch current subscription state from Stripe
    try:
        sub = client.v1.subscriptions.retrieve(record.stripe_subscription_id)
        sub_status = sub.status
    except stripe.StripeError as e:
        log.error(f"Stripe subscription retrieve error: {e}")
        sub_status = record.subscription_status

    # Fetch upcoming invoice (estimated cost for this billing cycle)
    upcoming_eur: Optional[float] = None
    try:
        invoice = client.v1.invoices.create_preview(
            params={"customer": record.stripe_customer_id}
        )
        upcoming_eur = invoice.amount_due / 100  # cents → euros
    except stripe.StripeError:
        pass  # No upcoming invoice yet (e.g. free tier covers it)

    has_pm = bool(record.stripe_payment_method_id)

    # Get current month cost from Langfuse
    current_month_cost = 0.0
    try:
        from open_webui.langfuse.metrics import get_current_month

        rows = get_current_month()
        user_rows = [r for r in rows if r.get("user") == user.email]
        current_month_cost = sum(r["cost"] for r in user_rows)
    except Exception as e:
        log.warning(f"Could not fetch Langfuse usage for billing status: {e}")

    return BillingStatusResponse(
        enabled=True,
        subscription_status=sub_status,
        current_month_cost_eur=current_month_cost,
        upcoming_invoice_eur=upcoming_eur,
        has_payment_method=has_pm,
        is_configured=True,
    )


@router.post("/setup", response_model=SetupResponse)
async def setup_billing(user=Depends(get_verified_user)):
    require_billing_enabled()
    client = get_stripe_client()

    if not STRIPE_PRICE_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STRIPE_PRICE_ID is not configured.",
        )

    existing = StripeBillings.get_by_user_id(user.id)

    # --- Create or reuse Stripe Customer ---
    if existing and existing.stripe_customer_id:
        customer_id = existing.stripe_customer_id
    else:
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

    # --- Apply free tier credit (once per customer) ---
    free_tier_applied = existing.free_tier_credit_applied if existing else False
    if not free_tier_applied and STRIPE_FREE_TIER_CENTS > 0:
        try:
            client.v1.customers.balance_transactions.create(
                customer_id,
                params={
                    "amount": -STRIPE_FREE_TIER_CENTS,  # negative = credit
                    "currency": "eur",
                    "description": "Free tier credit",
                },
            )
            free_tier_applied = True
        except stripe.StripeError as e:
            log.warning(f"Could not apply free tier credit: {e}")

    # --- Create subscription if not already existing ---
    if existing and existing.stripe_subscription_id:
        sub_id = existing.stripe_subscription_id
        sub_item_id = existing.stripe_subscription_item_id
        sub_status = existing.subscription_status
    else:
        try:
            sub = client.v1.subscriptions.create(
                params={
                    "customer": customer_id,
                    "items": [{"price": STRIPE_PRICE_ID}],
                    "currency": "eur",
                    "payment_behavior": "default_incomplete",
                    "expand": ["latest_invoice.payment_intent"],
                }
            )
            sub_id = sub.id
            sub_item_id = sub["items"].data[0].id
            sub_status = sub.status
        except stripe.StripeError as e:
            log.error(f"Stripe subscription create error: {e}")
            raise HTTPException(status_code=502, detail="Failed to create Stripe subscription.")

    # --- Persist to DB ---
    StripeBillings.upsert(
        user_id=user.id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=sub_id,
        stripe_subscription_item_id=sub_item_id,
        subscription_status=sub_status,
        free_tier_credit_applied=free_tier_applied,
    )

    return SetupResponse(is_configured=True, subscription_status=sub_status)


@router.post("/portal", response_model=PortalResponse)
async def billing_portal(request: Request, user=Depends(get_verified_user)):
    require_billing_enabled()
    client = get_stripe_client()

    record = StripeBillings.get_by_user_id(user.id)
    if not record or not record.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account found. Please set up billing first.",
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

    if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = getattr(data, "customer", None)
        new_status = getattr(data, "status", None)
        if customer_id and new_status:
            StripeBillings.update_subscription_status(customer_id, new_status)
            log.info(f"Subscription status updated: customer={customer_id} status={new_status}")

    elif event_type == "invoice.payment_failed":
        customer_id = getattr(data, "customer", None)
        if customer_id:
            StripeBillings.update_subscription_status(customer_id, "past_due")
            log.warning(f"Payment failed for customer={customer_id}")

    elif event_type == "invoice.payment_succeeded":
        customer_id = getattr(data, "customer", None)
        if customer_id:
            record = StripeBillings.get_by_customer_id(customer_id)
            if record and record.subscription_status == "past_due":
                StripeBillings.update_subscription_status(customer_id, "active")
                log.info(f"Payment recovered for customer={customer_id}")

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

    # Fetch all billing records and all users
    all_records = StripeBillings.get_all()
    billing_by_user_id = {r.user_id: r for r in all_records}

    # Fetch current-month Langfuse costs
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

    # Build summary for every user that has a billing record
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
                "subscription_status": record.subscription_status,
                "stripe_customer_id": record.stripe_customer_id,
                "current_month_cost_eur": round(cost_by_email.get(u.email, 0.0), 4),
                "free_tier_credit_applied": record.free_tier_credit_applied,
            }
        )

    # Also include users without a billing record so the admin has a full picture
    all_user_ids_with_billing = {r.user_id for r in all_records}
    for u in Users.get_users()["users"]:
        if u.id not in all_user_ids_with_billing:
            result.append(
                {
                    "user_id": u.id,
                    "name": u.name,
                    "email": u.email,
                    "subscription_status": None,
                    "stripe_customer_id": None,
                    "current_month_cost_eur": round(cost_by_email.get(u.email, 0.0), 4),
                    "free_tier_credit_applied": False,
                }
            )

    result.sort(key=lambda x: x["current_month_cost_eur"], reverse=True)
    return result
