import datetime
import logging
import time as _time
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from open_webui.env import (
    BILLING_ENABLED,
    INTERNAL_EMAIL_DOMAINS,
    STRIPE_FREE_TIER_CENTS,
    STRIPE_PRICE_ID,
    STRIPE_PRICE_ID_PRO,
    STRIPE_PRICE_ID_PREMIUM,
    STRIPE_SECRET_KEY,
    STRIPE_TEAM_TIERS,
    STRIPE_WEBHOOK_SECRET,
    TRIAL_CREDIT_EUR,
)
from open_webui.internal.db import get_db
from open_webui.models.billing import StripeBillings, TeamInvites, TeamMembers, Teams
from open_webui.models.billing_plans import (
    CREDITS_TIERS,
    PLAN_CREDITS,
    PLAN_TIER_INTERNAL,
    PLAN_TIER_PREMIUM,
    PLAN_TIER_PRO,
    PLAN_TIER_TEAM,
    PLAN_TIER_TEAM_MEMBER,
    PLAN_TIER_TRIAL,
)
from open_webui.utils.auth import get_admin_user, get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()

_TEAM_COST_CACHE_TTL = 45  # seconds


class _TTLCache:
    """Minimal bounded TTL cache — avoids an external dependency for a simple use case."""

    def __init__(self, maxsize: int, ttl: float) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: dict = {}   # key → (value, expires_at)

    def get(self, key, default=None):
        entry = self._store.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if _time.monotonic() >= expires_at:
            del self._store[key]
            return default
        return value

    def __setitem__(self, key, value) -> None:
        if len(self._store) >= self._maxsize and key not in self._store:
            # Evict one expired entry; if none expired, drop oldest
            now = _time.monotonic()
            expired = [k for k, (_, exp) in self._store.items() if exp <= now]
            if expired:
                del self._store[expired[0]]
            elif self._store:
                del self._store[next(iter(self._store))]
        self._store[key] = (value, _time.monotonic() + self._ttl)


_team_cost_cache: _TTLCache = _TTLCache(maxsize=256, ttl=_TEAM_COST_CACHE_TTL)


# ---------- Helpers ----------


def _tier_from_price_id(price_id: str) -> str:
    """Map a Stripe price ID to a plan tier string. Defaults to pro for unknown IDs."""
    if STRIPE_PRICE_ID_PREMIUM and price_id == STRIPE_PRICE_ID_PREMIUM:
        return PLAN_TIER_PREMIUM
    return PLAN_TIER_PRO


def _price_id_from_session(session) -> str:
    """Extract the first price ID from a Stripe checkout session object."""
    try:
        line_items = getattr(session, "line_items", None)
        if line_items:
            data = getattr(line_items, "data", None) or line_items.get("data", [])
            if data:
                price = getattr(data[0], "price", None) or data[0].get("price", {})
                return getattr(price, "id", None) or price.get("id", "")
    except Exception:
        pass
    meta = getattr(session, "metadata", None) or {}
    if hasattr(meta, "get"):
        return meta.get("price_id", "")
    return ""


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
    """Return total ledger cost (EUR) for `email` since account creation."""
    try:
        from open_webui.models.usage_ledger import UsageLedgerDB

        return UsageLedgerDB.get_cost_eur_for_user_since(email, created_at)
    except Exception as e:
        log.warning(f"Could not fetch alltime ledger cost for {email}: {e}")
        return 0.0


def _get_user_current_month_cost(email: str) -> float:
    try:
        from open_webui.models.usage_ledger import UsageLedgerDB

        return UsageLedgerDB.get_cost_eur_for_user_current_month(email)
    except Exception as e:
        log.warning(f"Could not fetch monthly ledger cost for {email}: {e}")
        return 0.0


def _check_credits_exhausted(email: str, user_id: str) -> None:
    """Raise 402 with detail='credits_exhausted' if the user has no remaining credits.

    Trial credits are lifetime (alltime cost). Pro/premium reset monthly.
    When no user_credits row exists, falls back to the plan's default balance so
    users who raced past onboarding are still enforced (not silently allowed through).
    """
    try:
        from open_webui.models.user_credits import CREDITS_PER_EUR_CENT, UserCreditsDB, eur_to_credits

        record = StripeBillings.get_by_user_id(user_id)
        if not record or record.plan_tier not in CREDITS_TIERS:
            return

        row = UserCreditsDB.get(email)
        balance = row.balance if row else PLAN_CREDITS.get(record.plan_tier, 0)
        rate = row.credits_per_eur_cent if row else CREDITS_PER_EUR_CENT

        if record.plan_tier == PLAN_TIER_TRIAL:
            cost_eur = _get_user_alltime_cost(email, record.created_at or 0)
        else:
            cost_eur = _get_user_current_month_cost(email)

        used = eur_to_credits(cost_eur, rate)
        if used >= balance:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="credits_exhausted",
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("[billing] credits exhaustion check failed for %s: %s", email, e)


def _get_team_current_month_cost(team_id: str) -> float:
    """Return aggregate current-month EUR cost for all members of a team (from ledger).

    Result is cached for _TEAM_COST_CACHE_TTL seconds.
    """
    cached = _team_cost_cache.get(team_id)
    if cached is not None:
        return cached

    try:
        from open_webui.models.usage_ledger import UsageLedgerDB
        from open_webui.models.users import Users as UsersModel

        members = TeamMembers.get_by_team_id(team_id)
        if not members:
            _team_cost_cache[team_id] = 0.0
            return 0.0

        emails = []
        for m in members:
            u = UsersModel.get_user_by_id(m.user_id)
            if u:
                emails.append(u.email)

        cost_by_email = UsageLedgerDB.get_cost_eur_for_users_current_month(emails)
        total = sum(cost_by_email.values())
        _team_cost_cache[team_id] = total
        return total
    except Exception as e:
        log.warning(f"Could not fetch team monthly cost for team_id={team_id}: {e}")
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
        # No billing record — onboard now (covers existing users who never signed up through billing flow)
        await auto_onboard_user(user)
        record = StripeBillings.get_by_user_id(user.id)
        if record is None:
            return user  # Stripe unreachable, allow through

    if record.plan_tier in (PLAN_TIER_PRO, PLAN_TIER_PREMIUM):
        if record.subscription_status in ("active", "trialing"):
            _check_credits_exhausted(user.email, user.id)
            return user
        if record.subscription_status == "canceled":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Your subscription has been canceled. Please visit /billing to reactivate.",
            )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Your payment is past due. Please update your billing details at /billing.",
        )

    if record.plan_tier == PLAN_TIER_TEAM:
        team = Teams.get_by_owner_user_id(user.id)
        if not team or team.subscription_status not in ("active", "trialing"):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Your team subscription is not active. Please visit /billing.",
            )
        if team.monthly_usage_budget_eur > 0:
            used = _get_team_current_month_cost(team.id)
            total_budget = team.monthly_usage_budget_eur + (team.top_up_credits or 0)
            if used >= total_budget:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=(
                        f"Team usage budget of €{total_budget:.2f} exceeded "
                        f"(used €{used:.4f}). Buy more usage at /billing."
                    ),
                )
        return user

    if record.plan_tier == PLAN_TIER_TEAM_MEMBER:
        if not record.team_id:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="You are not part of an active team. Please contact your team owner.",
            )
        team = Teams.get_by_id(record.team_id)
        if not team or team.subscription_status not in ("active", "trialing"):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Your team subscription is not active. Please contact your team owner.",
            )
        if team.monthly_usage_budget_eur > 0:
            used = _get_team_current_month_cost(team.id)
            total_budget = team.monthly_usage_budget_eur + (team.top_up_credits or 0)
            if used >= total_budget:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=(
                        "Your team's usage budget has been exceeded. "
                        "Ask your team owner to buy more usage at /billing."
                    ),
                )
        return user

    if record.plan_tier == PLAN_TIER_TRIAL:
        _check_credits_exhausted(user.email, user.id)
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
            plan_tier=PLAN_TIER_INTERNAL,
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
            plan_tier=PLAN_TIER_TRIAL,
            free_tier_credit_applied=free_tier_applied,
        )
        # Assign trial credits at current global rate
        from open_webui.models.user_credits import UserCreditsDB, CREDITS_PER_EUR_CENT
        from open_webui.models.billing_plans import get_trial_credits
        trial_credits = get_trial_credits(CREDITS_PER_EUR_CENT)
        UserCreditsDB.set_plan(user.email, trial_credits, CREDITS_PER_EUR_CENT)
        log.info(f"[billing] External user onboarded as trial: {user.email} (customer={customer_id}, credits={trial_credits})")

    except stripe.StripeError as e:
        log.error(f"[billing] Failed to onboard external user {user.email}: {e}")


# ---------- Response models ----------


class BillingStatusResponse(BaseModel):
    enabled: bool
    plan_tier: Optional[str] = None  # internal | trial | paid | team | team_member | None

    # Trial fields
    credit_limit_eur: float = 0.0
    credit_used_eur: float = 0.0
    credit_remaining_eur: float = 0.0

    # Paid / team subscription fields
    subscription_status: Optional[str] = None
    upcoming_invoice_eur: Optional[float] = None

    # Team fields (owner view)
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    seat_limit: Optional[int] = None
    seat_used: Optional[int] = None
    team_month_cost_eur: Optional[float] = None  # aggregate cost for owner view

    # Team usage budget fields (owner + member view)
    usage_budget_eur: Optional[float] = None       # monthly included budget
    extra_credit_eur: Optional[float] = None       # purchased top-up credits
    usage_budget_remaining_eur: Optional[float] = None  # budget + extra - used

    # Team member view
    team_owner_name: Optional[str] = None

    # Current month usage (all tiers — individual for members)
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
        # Existing user pre-dates billing system — onboard them now and re-fetch
        await auto_onboard_user(user)
        record = StripeBillings.get_by_user_id(user.id)

    if not record:
        # Onboarding failed (e.g. Stripe unreachable) — return unconfigured state
        return BillingStatusResponse(
            enabled=True,
            is_configured=False,
            current_month_cost_eur=current_month_cost,
        )

    if record.plan_tier == PLAN_TIER_INTERNAL:
        return BillingStatusResponse(
            enabled=True,
            plan_tier=PLAN_TIER_INTERNAL,
            is_configured=True,
            current_month_cost_eur=current_month_cost,
        )

    if record.plan_tier == PLAN_TIER_TRIAL:
        cost_used = _get_user_alltime_cost(user.email, record.created_at)
        credit_limit = TRIAL_CREDIT_EUR
        remaining = max(0.0, credit_limit - cost_used)
        return BillingStatusResponse(
            enabled=True,
            plan_tier=PLAN_TIER_TRIAL,
            is_configured=True,
            credit_limit_eur=credit_limit,
            credit_used_eur=round(cost_used, 4),
            credit_remaining_eur=round(remaining, 4),
            current_month_cost_eur=current_month_cost,
        )

    if record.plan_tier == PLAN_TIER_TEAM:
        team = Teams.get_by_owner_user_id(user.id)
        if not team:
            return BillingStatusResponse(
                enabled=True,
                plan_tier=PLAN_TIER_TEAM,
                is_configured=False,
                current_month_cost_eur=current_month_cost,
            )

        seat_used = TeamMembers.count_members(team.id)

        from open_webui.models.users import Users as UsersModel
        from open_webui.models.usage_ledger import UsageLedgerDB

        members_db = TeamMembers.get_by_team_id(team.id)
        member_emails = []
        for m in members_db:
            u = UsersModel.get_user_by_id(m.user_id)
            if u:
                member_emails.append(u.email)
        if user.email not in member_emails:
            member_emails.append(user.email)

        try:
            cost_by_email = UsageLedgerDB.get_cost_eur_for_users_current_month(member_emails)
            team_month_cost = sum(cost_by_email.values())
        except Exception:
            team_month_cost = current_month_cost

        upcoming_eur: Optional[float] = None
        sub_status = team.subscription_status
        if team.stripe_subscription_id:
            try:
                client = get_stripe_client()
                sub = client.v1.subscriptions.retrieve(team.stripe_subscription_id)
                sub_status = sub.status
            except stripe.StripeError:
                pass
            try:
                client = get_stripe_client()
                invoice = client.v1.invoices.create_preview(
                    params={"customer": team.stripe_customer_id, "subscription": team.stripe_subscription_id}
                )
                upcoming_eur = invoice.amount_due / 100
            except stripe.StripeError:
                pass

        budget = team.monthly_usage_budget_eur or 0.0
        extra = team.top_up_credits or 0
        remaining = max(0.0, budget + extra - team_month_cost) if budget > 0 else None

        return BillingStatusResponse(
            enabled=True,
            plan_tier=PLAN_TIER_TEAM,
            is_configured=True,
            subscription_status=sub_status,
            upcoming_invoice_eur=upcoming_eur,
            team_id=team.id,
            team_name=team.name,
            seat_limit=team.seat_limit,
            seat_used=seat_used,
            team_month_cost_eur=round(team_month_cost, 4),
            usage_budget_eur=budget if budget > 0 else None,
            extra_credit_eur=extra if extra > 0 else None,
            usage_budget_remaining_eur=round(remaining, 4) if remaining is not None else None,
            current_month_cost_eur=current_month_cost,
        )

    if record.plan_tier == PLAN_TIER_TEAM_MEMBER:
        if not record.team_id:
            return BillingStatusResponse(
                enabled=True,
                plan_tier=PLAN_TIER_TEAM_MEMBER,
                is_configured=False,
                current_month_cost_eur=current_month_cost,
            )
        team = Teams.get_by_id(record.team_id)
        if not team:
            return BillingStatusResponse(
                enabled=True,
                plan_tier=PLAN_TIER_TEAM_MEMBER,
                is_configured=False,
                current_month_cost_eur=current_month_cost,
            )
        from open_webui.models.users import Users as UsersModel

        owner = UsersModel.get_user_by_id(team.owner_user_id)
        budget = team.monthly_usage_budget_eur or 0.0
        extra = team.top_up_credits or 0
        team_month_cost = _get_team_current_month_cost(team.id)
        remaining = max(0.0, budget + extra - team_month_cost) if budget > 0 else None
        return BillingStatusResponse(
            enabled=True,
            plan_tier=PLAN_TIER_TEAM_MEMBER,
            is_configured=True,
            subscription_status=team.subscription_status,
            team_id=team.id,
            team_name=team.name,
            team_owner_name=owner.name if owner else None,
            usage_budget_eur=budget if budget > 0 else None,
            extra_credit_eur=extra if extra > 0 else None,
            usage_budget_remaining_eur=round(remaining, 4) if remaining is not None else None,
            current_month_cost_eur=current_month_cost,
        )

    # Fallback for legacy rows without plan_tier — treat as internal
    return BillingStatusResponse(
        enabled=True,
        plan_tier=PLAN_TIER_INTERNAL,
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

    if record and record.plan_tier == PLAN_TIER_INTERNAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Internal users do not need a subscription.",
        )

    client = get_stripe_client()

    # Reuse existing Stripe customer if available
    customer_id = record.stripe_customer_id if record else None

    webui_url = (request.app.state.WEBUI_URL or str(request.base_url)).rstrip("/")

    def _create_customer() -> str:
        customer = client.v1.customers.create(
            params={
                "email": user.email,
                "name": user.name,
                "metadata": {"user_id": user.id},
            }
        )
        return customer.id

    if not customer_id:
        try:
            customer_id = _create_customer()
        except stripe.StripeError as e:
            log.error(f"Stripe customer create error: {e}")
            raise HTTPException(status_code=502, detail="Failed to create Stripe customer.")

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
        error_str = str(e)
        if "No such customer" in error_str:
            # Customer exists in DB but not in this Stripe environment (e.g. test vs live)
            log.warning(f"[billing] Customer {customer_id} not found in Stripe, creating a new one.")
            try:
                customer_id = _create_customer()
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
            except stripe.StripeError as e2:
                log.error(f"Stripe checkout session create error after customer retry: {e2}")
                raise HTTPException(status_code=502, detail="Failed to create checkout session.")
        else:
            log.error(f"Stripe checkout session create error: {e}")
            raise HTTPException(status_code=502, detail="Failed to create checkout session.")

    StripeBillings.upsert(
        user_id=user.id,
        stripe_customer_id=customer_id,
        plan_tier=record.plan_tier if record else "trial",
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

    webui_url = (request.app.state.WEBUI_URL or str(request.base_url)).rstrip("/")
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


def _revert_team_members_to_trial(stripe_customer_id: str) -> None:
    """Revert all members of a team to trial when the team subscription is canceled."""
    team = Teams.get_by_customer_id(stripe_customer_id)
    if not team:
        return
    members = TeamMembers.get_by_team_id(team.id)
    for m in members:
        if m.role != "owner":
            StripeBillings.revert_to_trial(m.user_id)
    log.info(f"[billing] Reverted {len(members)} team members to trial for team {team.id}")


# ---------- Team response models ----------


class TeamCreateRequest(BaseModel):
    name: str
    seat_count: int


class TeamInviteRequest(BaseModel):
    email: str


class TeamMemberResponse(BaseModel):
    user_id: str
    name: str
    email: str
    role: str
    current_month_cost_eur: float = 0.0
    models_used: list[str] = []


class TeamInviteResponse(BaseModel):
    id: str
    invited_email: str
    status: str
    token: str
    expires_at: int


class TeamStatusResponse(BaseModel):
    team_id: str
    name: str
    seat_limit: int
    seat_used: int
    subscription_status: Optional[str]
    members: list[TeamMemberResponse]
    pending_invites: list[TeamInviteResponse]
    team_month_cost_eur: float = 0.0


# ---------- Team endpoints ----------


@router.get("/team/tiers")
async def get_team_tiers(user=Depends(get_verified_user)):
    """Return available team subscription tiers (seat count + price)."""
    require_billing_enabled()
    return [
        {"seat_count": int(seats), "price_eur": float(cfg.get("price_eur", 0))}
        for seats, cfg in STRIPE_TEAM_TIERS.items()
        if cfg.get("price_id")
    ]


@router.post("/team/create", response_model=CheckoutResponse)
async def create_team(body: TeamCreateRequest, request: Request, user=Depends(get_verified_user)):
    """Create a team and return a Stripe checkout URL for the team subscription."""
    require_billing_enabled()

    if not STRIPE_TEAM_TIERS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STRIPE_TEAM_TIERS is not configured.",
        )

    seat_count = body.seat_count
    tier_config = STRIPE_TEAM_TIERS.get(str(seat_count))
    if not tier_config or not tier_config.get("price_id"):
        available = list(STRIPE_TEAM_TIERS.keys())
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid seat count. Available tiers: {available}",
        )

    # Prevent creating multiple teams
    existing_team = Teams.get_by_owner_user_id(user.id)
    if existing_team:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have a team. Manage it at /billing.",
        )

    client = get_stripe_client()
    webui_url = (request.app.state.WEBUI_URL or str(request.base_url)).rstrip("/")

    # Create Stripe customer for the team
    try:
        customer = client.v1.customers.create(
            params={
                "email": user.email,
                "name": f"{body.name} (Team)",
                "metadata": {"user_id": user.id, "team_name": body.name},
            }
        )
        customer_id = customer.id
    except stripe.StripeError as e:
        log.error(f"[billing] Team customer create error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create Stripe customer.")

    # Create checkout session before persisting the team — avoids orphaned rows on Stripe failure
    try:
        session = client.v1.checkout.sessions.create(
            params={
                "customer": customer_id,
                "mode": "subscription",
                "line_items": [{"price": tier_config["price_id"], "quantity": 1}],
                "success_url": f"{webui_url}/billing?checkout=success",
                "cancel_url": f"{webui_url}/billing?checkout=canceled",
                "metadata": {"user_id": user.id},
            }
        )
    except stripe.StripeError as e:
        log.error(f"[billing] Team checkout session error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create checkout session.")

    # Stripe session created successfully — now persist team record
    try:
        team = Teams.create(
            name=body.name,
            owner_user_id=user.id,
            seat_limit=seat_count,
        )
        Teams.update(team.id, stripe_customer_id=customer_id)
        TeamMembers.add(team.id, user.id, role="owner")
    except Exception as e:
        log.error(f"[billing] Team DB persist failed after Stripe session created (customer={customer_id}, session={session.id}): {e}")
        raise HTTPException(status_code=500, detail="Failed to save team record. Contact support.")

    return CheckoutResponse(url=session.url)


@router.get("/team", response_model=TeamStatusResponse)
async def get_team_status(user=Depends(get_verified_user)):
    """Get team info for the team owner."""
    require_billing_enabled()

    team = Teams.get_by_owner_user_id(user.id)
    if not team:
        raise HTTPException(status_code=404, detail="No team found.")

    from open_webui.models.users import Users as UsersModel

    members_db = TeamMembers.get_by_team_id(team.id)
    pending_invites = TeamInvites.get_by_team_id(team.id)

    from open_webui.models.usage_ledger import UsageLedgerDB

    user_by_id = {
        m.user_id: u
        for m in members_db
        if (u := UsersModel.get_user_by_id(m.user_id))
    }
    member_emails = [u.email for u in user_by_id.values()]
    try:
        cost_by_email = UsageLedgerDB.get_cost_eur_for_users_current_month(member_emails)
        models_by_email = UsageLedgerDB.get_models_used_bulk_current_month(member_emails)
    except Exception:
        cost_by_email = {}
        models_by_email = {}

    members_out: list[TeamMemberResponse] = []
    team_month_cost = 0.0
    for m in members_db:
        u = user_by_id.get(m.user_id)
        if not u:
            continue
        cost = cost_by_email.get(u.email, 0.0)
        team_month_cost += cost
        members_out.append(
            TeamMemberResponse(
                user_id=u.id,
                name=u.name,
                email=u.email,
                role=m.role,
                current_month_cost_eur=round(cost, 4),
                models_used=models_by_email.get(u.email, []),
            )
        )

    invites_out = [
        TeamInviteResponse(
            id=inv.id,
            invited_email=inv.invited_email,
            status=inv.status,
            token=inv.token,
            expires_at=inv.expires_at,
        )
        for inv in pending_invites
        if inv.status == "pending"
    ]

    return TeamStatusResponse(
        team_id=team.id,
        name=team.name,
        seat_limit=team.seat_limit,
        seat_used=len(members_db),
        subscription_status=team.subscription_status,
        members=members_out,
        pending_invites=invites_out,
        team_month_cost_eur=round(team_month_cost, 4),
    )


@router.post("/team/invite")
async def invite_team_member(body: TeamInviteRequest, user=Depends(get_verified_user)):
    """Invite a user to the team by email."""
    require_billing_enabled()

    team = Teams.get_by_owner_user_id(user.id)
    if not team:
        raise HTTPException(status_code=404, detail="No team found.")

    if team.subscription_status not in ("active", "trialing"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Team subscription is not active.",
        )

    current_member_count = TeamMembers.count_members(team.id)
    if current_member_count >= team.seat_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Seat limit reached ({team.seat_limit} seats).",
        )

    email = body.email.lower().strip()

    # Check if already a member
    from open_webui.models.users import Users as UsersModel

    existing_user = UsersModel.get_user_by_email(email)
    if existing_user:
        existing_member = TeamMembers.get_by_user_id(existing_user.id)
        if existing_member and existing_member.team_id == team.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This user is already a team member.",
            )

    # Remove any previous pending invite for the same email
    TeamInvites.delete_pending_by_email_and_team(team.id, email)

    invite = TeamInvites.create(
        team_id=team.id,
        invited_email=email,
        invited_by=user.id,
    )

    # Send invite email (silently skipped if SMTP not configured)
    try:
        import os
        from open_webui.utils.email import send_team_invite_email

        webui_url = os.environ.get("WEBUI_URL", "http://localhost:5173").rstrip("/")
        invite_url = f"{webui_url}/invite/{invite.token}"
        send_team_invite_email(
            to=email,
            team_name=team.name,
            invited_by=user.name or user.email,
            invite_url=invite_url,
        )
    except Exception as e:
        log.warning(f"[billing] Could not send invite email to {email}: {e}")

    return {
        "invite_id": invite.id,
        "token": invite.token,
        "invited_email": invite.invited_email,
        "expires_at": invite.expires_at,
    }


@router.delete("/team/members/{member_user_id}")
async def remove_team_member(member_user_id: str, user=Depends(get_verified_user)):
    """Remove a member from the team and revert them to trial."""
    require_billing_enabled()

    team = Teams.get_by_owner_user_id(user.id)
    if not team:
        raise HTTPException(status_code=404, detail="No team found.")

    if member_user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner cannot remove themselves from the team.",
        )

    removed = TeamMembers.remove(team.id, member_user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found.")

    StripeBillings.revert_to_trial(member_user_id)

    return {"removed": True}


@router.post("/team/portal", response_model=PortalResponse)
async def team_billing_portal(request: Request, user=Depends(get_verified_user)):
    """Stripe billing portal for the team owner."""
    require_billing_enabled()

    team = Teams.get_by_owner_user_id(user.id)
    if not team or not team.stripe_customer_id:
        raise HTTPException(status_code=404, detail="No team billing account found.")

    client = get_stripe_client()
    webui_url = (request.app.state.WEBUI_URL or str(request.base_url)).rstrip("/")
    try:
        session = client.v1.billing_portal.sessions.create(
            params={
                "customer": team.stripe_customer_id,
                "return_url": f"{webui_url}/billing",
            }
        )
    except stripe.StripeError as e:
        log.error(f"[billing] Team portal error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create billing portal session.")

    return PortalResponse(url=session.url)


# ---------- Top-up (paid users + teams) ----------


class TopupRequest(BaseModel):
    top_up_id: str


@router.get("/topup/options")
async def get_topup_options(user=Depends(get_verified_user)):
    """Return available top-up packs from DB."""
    require_billing_enabled()
    from open_webui.models.topup import TopupPacks

    packs = TopupPacks.get_all()
    return [{"id": p.id, "credits": p.credits, "price_eur": p.price_eur} for p in packs]


@router.post("/topup", response_model=CheckoutResponse)
async def create_topup(body: TopupRequest, request: Request, user=Depends(get_verified_user)):
    """Create a one-time Stripe checkout for a top-up pack (paid user or team)."""
    require_billing_enabled()

    from open_webui.models.topup import TopupPacks

    billing = StripeBillings.get_by_user_id(user.id)
    if not billing or billing.plan_tier not in (PLAN_TIER_PRO, PLAN_TIER_PREMIUM, PLAN_TIER_TEAM):
        # Explicitly reject team_member, trial, internal, and any other tier
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Top-up requires an active paid or team plan.",
        )

    if billing.plan_tier in (PLAN_TIER_PRO, PLAN_TIER_PREMIUM) and billing.subscription_status not in ("active", "trialing"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Paid subscription is not active.",
        )

    pack = TopupPacks.get_by_id(body.top_up_id)
    if not pack:
        raise HTTPException(status_code=400, detail="Invalid top_up_id.")

    if billing.plan_tier == PLAN_TIER_TEAM:
        team = Teams.get_by_owner_user_id(user.id)
        if not team or team.subscription_status not in ("active", "trialing"):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Team subscription is not active.",
            )
        if not team.stripe_customer_id:
            raise HTTPException(status_code=400, detail="No billing account.")
        target_customer_id = team.stripe_customer_id
        target_id = team.id
        is_team = True
    else:
        if not billing.stripe_customer_id:
            raise HTTPException(status_code=400, detail="No billing account.")
        target_customer_id = billing.stripe_customer_id
        target_id = user.id
        is_team = False

    client = get_stripe_client()
    webui_url = (request.app.state.WEBUI_URL or str(request.base_url)).rstrip("/")

    try:
        session = client.v1.checkout.sessions.create(
            params={
                "customer": target_customer_id,
                "mode": "payment",
                "line_items": [{"price": pack.stripe_price_id, "quantity": 1}],
                "success_url": f"{webui_url}/billing?topup=success",
                "cancel_url": f"{webui_url}/billing?topup=canceled",
                "metadata": {
                    "type": "topup",
                    "top_up_id": body.top_up_id,
                    **({"team_id": target_id} if is_team else {"user_id": target_id}),
                },
            }
        )
    except stripe.StripeError as e:
        log.error(f"[billing] Topup checkout error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create top-up checkout.")

    # Idempotency is handled in the webhook via stripe_purchase_history.stripe_checkout_session_id.

    return CheckoutResponse(url=session.url)


# ---------- Team invite acceptance ----------


@router.get("/invite/{token}")
async def get_invite_info(token: str, user=Depends(get_verified_user)):
    """Return invite metadata so the frontend can display team name before accepting."""
    require_billing_enabled()

    invite = TeamInvites.get_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found.")
    if invite.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invite is already {invite.status}.",
        )

    import time as _time

    if _time.time() > invite.expires_at:
        TeamInvites.update_status(token, "expired")
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite has expired.")

    if user.email.lower() != invite.invited_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite is for a different email address.",
        )

    team = Teams.get_by_id(invite.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    from open_webui.models.users import Users as UsersModel

    owner = UsersModel.get_user_by_id(team.owner_user_id)
    return {
        "team_id": team.id,
        "team_name": team.name,
        "owner_name": owner.name if owner else None,
        "invited_email": invite.invited_email,
        "expires_at": invite.expires_at,
    }


@router.post("/invite/{token}/accept")
async def accept_invite(token: str, user=Depends(get_verified_user)):
    """Accept a team invite — adds user to the team."""
    require_billing_enabled()

    invite = TeamInvites.get_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found.")
    if invite.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invite is already {invite.status}.",
        )

    import time as _time

    if _time.time() > invite.expires_at:
        TeamInvites.update_status(token, "expired")
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite has expired.")

    if user.email.lower() != invite.invited_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite is for a different email address.",
        )

    team = Teams.get_by_id(invite.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    if team.subscription_status not in ("active", "trialing"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Team subscription is not active.",
        )

    current_count = TeamMembers.count_members(team.id)
    if current_count >= team.seat_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team has no available seats.",
        )

    # Check not already a member of a different team
    existing = TeamMembers.get_by_user_id(user.id)
    if existing and existing.team_id != team.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of a different team.",
        )
    if not existing:
        TeamMembers.add(team.id, user.id, role="member")

    # Update user's billing record
    StripeBillings.upsert(
        user_id=user.id,
        plan_tier=PLAN_TIER_TEAM_MEMBER,
        team_id=team.id,
        free_tier_credit_applied=True,  # preserve flag so they don't get another trial credit
    )

    TeamInvites.update_status(token, "accepted")

    return {"accepted": True, "team_id": team.id, "team_name": team.name}


@router.post("/invite/{token}/decline")
async def decline_invite(token: str, user=Depends(get_verified_user)):
    """Decline a team invite."""
    require_billing_enabled()

    invite = TeamInvites.get_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found.")
    if invite.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invite is already {invite.status}.",
        )

    if user.email.lower() != invite.invited_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite is for a different email address.",
        )

    TeamInvites.update_status(token, "declined")
    return {"declined": True}


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

    try:
        return await _handle_stripe_event(event_type, data)
    except Exception as exc:
        log.exception(f"[billing] Webhook handler crashed for event_type={event_type}: {exc}")
        raise HTTPException(status_code=500, detail="Webhook handler error")


async def _handle_stripe_event(event_type: str, data):
    if event_type == "checkout.session.completed":
        session_id = getattr(data, "id", None)
        customer_id = getattr(data, "customer", None)
        subscription_id = getattr(data, "subscription", None)
        raw_meta = getattr(data, "metadata", None)
        if raw_meta is None:
            metadata = {}
        else:
            try:
                metadata = dict(raw_meta)
            except Exception:
                metadata = getattr(raw_meta, "_data", {}) or {}

        # ── Top-up (team or individual) ────────────────────────────────────
        if metadata.get("type") == "topup":
            from open_webui.models.topup import TopupPacks
            from open_webui.models.credit_balances import CreditBalances
            from open_webui.models.purchase_history import PurchaseHistory

            team_id = metadata.get("team_id")
            user_id = metadata.get("user_id")
            top_up_id = metadata.get("top_up_id")

            # Require successful payment capture before crediting
            if getattr(data, "payment_status", None) != "paid":
                return {"received": True}

            # Idempotency via purchase_history
            if session_id and PurchaseHistory.already_processed(stripe_checkout_session_id=session_id):
                return {"received": True}

            pack = TopupPacks.get_by_id(top_up_id) if top_up_id else None
            if pack:
                credits = pack.credits
                if team_id:
                    CreditBalances.add_topup("team", team_id, credits)
                    team = Teams.get_by_id(team_id)
                    owner_user_id = team.owner_user_id if team else (user_id or "")
                    PurchaseHistory.insert(
                        user_id=owner_user_id,
                        event_type="topup",
                        team_id=team_id,
                        stripe_customer_id=customer_id,
                        stripe_checkout_session_id=session_id,
                        topup_credits_granted=credits,
                        amount_eur=pack.price_eur,
                    )
                    log.info("[billing] Team topup: team_id=%s credits=%d", team_id, credits)
                elif user_id:
                    from open_webui.models.users import Users as _Users
                    u = _Users.get_user_by_id(user_id)
                    owner_id = u.email if u else user_id
                    CreditBalances.add_topup("user", owner_id, credits)
                    PurchaseHistory.insert(
                        user_id=user_id,
                        event_type="topup",
                        stripe_customer_id=customer_id,
                        stripe_checkout_session_id=session_id,
                        topup_credits_granted=credits,
                        amount_eur=pack.price_eur,
                    )
                    log.info("[billing] User topup: user_id=%s credits=%d", user_id, credits)
            return {"received": True}

        # ── Team subscription checkout ──────────────────────────────────────
        # Match team by customer_id (checkout_session_id column no longer exists)
        team = Teams.get_by_customer_id(customer_id) if customer_id else None

        if team:
            from open_webui.models.credit_balances import CreditBalances
            from open_webui.models.purchase_history import PurchaseHistory
            from open_webui.models.stripe_packages import StripePackages

            price_id = _price_id_from_session(data)
            pkg = StripePackages.get_by_price_id(price_id) if price_id else None
            credits = pkg.credits if pkg else 0
            plan_tier = pkg.plan_tier if pkg else PLAN_TIER_TEAM

            Teams.update(
                team.id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                subscription_status="active",
                monthly_credits=credits,
            )
            StripeBillings.upsert(
                user_id=team.owner_user_id,
                stripe_customer_id=customer_id,
                plan_tier=PLAN_TIER_TEAM,
                subscription_status="active",
                team_id=team.id,
            )

            from open_webui.models.user_credits import CREDITS_PER_EUR_CENT
            existing_bal = CreditBalances.get("team", team.id)
            rate = existing_bal.credits_per_eur_cent if existing_bal else CREDITS_PER_EUR_CENT
            CreditBalances.set_subscription(
                owner_type="team",
                owner_id=team.id,
                credits=credits,
                credits_per_eur_cent=rate,
                period_start=int(_time.time()),
            )
            PurchaseHistory.insert(
                user_id=team.owner_user_id,
                event_type="subscription_start",
                team_id=team.id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                stripe_checkout_session_id=session_id,
                plan_tier=plan_tier,
                package_id=pkg.id if pkg else None,
                subscription_credits_granted=credits,
                amount_eur=pkg.price_eur if pkg else None,
            )
            log.info("[billing] Team checkout completed: team_id=%s plan=%s credits=%d", team.id, plan_tier, credits)

        else:
            # ── Individual user subscription checkout ───────────────────────
            record = StripeBillings.get_by_customer_id(customer_id) if customer_id else None

            if record:
                from open_webui.models.credit_balances import CreditBalances
                from open_webui.models.purchase_history import PurchaseHistory
                from open_webui.models.stripe_packages import StripePackages
                from open_webui.models.users import Users as _Users
                from open_webui.models.user_credits import CREDITS_PER_EUR_CENT

                price_id = _price_id_from_session(data)
                pkg = StripePackages.get_by_price_id(price_id) if price_id else None
                plan_tier = (pkg.plan_tier if pkg else None) or _tier_from_price_id(price_id)
                credits = pkg.credits if pkg else PLAN_CREDITS.get(plan_tier, PLAN_CREDITS[PLAN_TIER_PRO])

                StripeBillings.upsert(
                    user_id=record.user_id,
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    plan_tier=plan_tier,
                    subscription_status="active",
                    free_tier_credit_applied=record.free_tier_credit_applied,
                )

                u = _Users.get_user_by_id(record.user_id)
                owner_id = u.email if u else record.user_id
                existing_bal = CreditBalances.get("user", owner_id)
                rate = existing_bal.credits_per_eur_cent if existing_bal else CREDITS_PER_EUR_CENT
                CreditBalances.set_subscription(
                    owner_type="user",
                    owner_id=owner_id,
                    credits=credits,
                    credits_per_eur_cent=rate,
                    period_start=int(_time.time()),
                )
                PurchaseHistory.insert(
                    user_id=record.user_id,
                    event_type="subscription_start",
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    stripe_checkout_session_id=session_id,
                    plan_tier=plan_tier,
                    package_id=pkg.id if pkg else None,
                    subscription_credits_granted=credits,
                    amount_eur=pkg.price_eur if pkg else None,
                )
                log.info("[billing] Checkout completed: user_id=%s plan=%s credits=%d", record.user_id, plan_tier, credits)

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = getattr(data, "customer", None)
        new_status = getattr(data, "status", None)
        if customer_id and new_status:
            if not Teams.update_subscription_status(customer_id, new_status):
                StripeBillings.update_subscription_status(customer_id, new_status)

            if event_type == "customer.subscription.deleted" or new_status == "canceled":
                from open_webui.models.credit_balances import CreditBalances
                from open_webui.models.purchase_history import PurchaseHistory
                from open_webui.models.users import Users as _Users

                team = Teams.get_by_customer_id(customer_id)
                if team:
                    CreditBalances.reset_all("team", team.id)
                    PurchaseHistory.insert(
                        user_id=team.owner_user_id,
                        event_type="cancellation",
                        team_id=team.id,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=getattr(data, "id", None),
                    )
                else:
                    record = StripeBillings.get_by_customer_id(customer_id)
                    if record:
                        u = _Users.get_user_by_id(record.user_id)
                        owner_id = u.email if u else record.user_id
                        CreditBalances.reset_all("user", owner_id)
                        PurchaseHistory.insert(
                            user_id=record.user_id,
                            event_type="cancellation",
                            stripe_customer_id=customer_id,
                            stripe_subscription_id=getattr(data, "id", None),
                            plan_tier=record.plan_tier,
                        )

                _revert_team_members_to_trial(customer_id)
            log.info("[billing] Subscription updated: customer=%s status=%s", customer_id, new_status)

    elif event_type == "invoice.payment_failed":
        customer_id = getattr(data, "customer", None)
        if customer_id:
            if not Teams.update_subscription_status(customer_id, "past_due"):
                StripeBillings.update_subscription_status(customer_id, "past_due")
            log.warning("[billing] Payment failed: customer=%s", customer_id)

    elif event_type == "invoice.paid":
        customer_id = getattr(data, "customer", None)
        invoice_id = getattr(data, "id", None)
        billing_reason = getattr(data, "billing_reason", None)

        # The initial invoice fires right after checkout.session.completed which already
        # set credits. Skip it here to avoid a duplicate renewal history entry.
        if billing_reason == "subscription_create":
            return {"received": True}

        if customer_id:
            from open_webui.models.credit_balances import CreditBalances
            from open_webui.models.purchase_history import PurchaseHistory
            from open_webui.models.stripe_packages import StripePackages
            from open_webui.models.users import Users as _Users
            from open_webui.models.user_credits import CREDITS_PER_EUR_CENT

            # Idempotency via invoice_id
            if invoice_id and PurchaseHistory.already_processed(stripe_invoice_id=invoice_id):
                return {"received": True}

            subscription_id = getattr(data, "subscription", None)
            amount_paid_cents = getattr(data, "amount_paid", None)
            amount_eur = amount_paid_cents / 100 if amount_paid_cents is not None else None

            # Resolve price → package from invoice line items
            price_id = None
            try:
                lines = getattr(data, "lines", None)
                if lines:
                    line_data = getattr(lines, "data", None) or lines.get("data", [])
                    if line_data:
                        price = getattr(line_data[0], "price", None) or line_data[0].get("price", {})
                        price_id = getattr(price, "id", None) or price.get("id", "")
            except Exception:
                pass
            pkg = StripePackages.get_by_price_id(price_id) if price_id else None

            team = Teams.get_by_customer_id(customer_id)
            if team:
                if team.subscription_status == "past_due":
                    Teams.update_subscription_status(customer_id, "active")
                    log.info("[billing] Team payment recovered: customer=%s", customer_id)
                if pkg:
                    credits = pkg.credits
                    existing_bal = CreditBalances.get("team", team.id)
                    rate = existing_bal.credits_per_eur_cent if existing_bal else CREDITS_PER_EUR_CENT
                    CreditBalances.set_subscription("team", team.id, credits, rate, int(_time.time()))
                    Teams.update(team.id, monthly_credits=credits)
                    PurchaseHistory.insert(
                        user_id=team.owner_user_id,
                        event_type="renewal",
                        team_id=team.id,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=subscription_id,
                        stripe_invoice_id=invoice_id,
                        plan_tier=pkg.plan_tier,
                        package_id=pkg.id,
                        subscription_credits_granted=credits,
                        amount_eur=amount_eur,
                    )
                    log.info("[billing] Team renewal credits reset: team_id=%s credits=%d", team.id, credits)
            else:
                record = StripeBillings.get_by_customer_id(customer_id)
                if record:
                    if record.subscription_status == "past_due":
                        StripeBillings.update_subscription_status(customer_id, "active")
                        log.info("[billing] Payment recovered: customer=%s", customer_id)
                    if record.plan_tier in (PLAN_TIER_PRO, PLAN_TIER_PREMIUM):
                        u = _Users.get_user_by_id(record.user_id)
                        if u:
                            credits = pkg.credits if pkg else PLAN_CREDITS.get(record.plan_tier, PLAN_CREDITS[PLAN_TIER_PRO])
                            existing_bal = CreditBalances.get("user", u.email)
                            rate = existing_bal.credits_per_eur_cent if existing_bal else CREDITS_PER_EUR_CENT
                            CreditBalances.set_subscription("user", u.email, credits, rate, int(_time.time()))
                            PurchaseHistory.insert(
                                user_id=record.user_id,
                                event_type="renewal",
                                stripe_customer_id=customer_id,
                                stripe_subscription_id=subscription_id,
                                stripe_invoice_id=invoice_id,
                                plan_tier=record.plan_tier,
                                package_id=pkg.id if pkg else None,
                                subscription_credits_granted=credits,
                                amount_eur=amount_eur,
                            )
                            log.info("[billing] Monthly renewal credits reset: user=%s plan=%s credits=%d", u.email, record.plan_tier, credits)

    elif event_type == "payment_method.attached":
        customer_id = getattr(data, "customer", None)
        pm_id = getattr(data, "id", None)
        if customer_id and pm_id:
            team = Teams.get_by_customer_id(customer_id)
            if team:
                Teams.update(team.id, stripe_payment_method_id=pm_id)
            else:
                record = StripeBillings.get_by_customer_id(customer_id)
                if record:
                    StripeBillings.upsert(
                        user_id=record.user_id,
                        stripe_payment_method_id=pm_id,
                        free_tier_credit_applied=record.free_tier_credit_applied,
                    )

    return {"received": True}


@router.post("/admin/test-email")
async def test_email(request: Request, user=Depends(get_admin_user)):
    """Send a test email to the logged-in admin to verify SMTP config."""
    from open_webui.utils.email import send_email
    from open_webui.env import SMTP_HOST, SMTP_FROM_EMAIL
    if not SMTP_HOST or not SMTP_FROM_EMAIL:
        raise HTTPException(status_code=400, detail=f"SMTP not configured (SMTP_HOST={SMTP_HOST!r}, SMTP_FROM_EMAIL={SMTP_FROM_EMAIL!r})")
    ok = send_email(user.email, "Keeper AI — SMTP test", "<p>SMTP is working correctly.</p>")
    if not ok:
        raise HTTPException(status_code=500, detail="Email send failed — check backend logs for [email] error")
    return {"sent": True, "to": user.email}


@router.get("/admin/summary")
async def admin_billing_summary(user=Depends(get_admin_user)):
    require_billing_enabled()

    from open_webui.models.users import Users

    all_records = StripeBillings.get_all()
    billing_by_user_id = {r.user_id: r for r in all_records}

    from open_webui.models.usage_ledger import UsageLedgerDB

    cost_by_email = UsageLedgerDB.get_all_users_cost_current_month()

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


@router.get("/model-breakdown")
async def get_model_breakdown(user=Depends(get_verified_user)):
    """Per-model EUR cost breakdown for the current user this calendar month."""
    require_billing_enabled()

    from open_webui.models.usage_ledger import UsageLedgerDB

    rows = UsageLedgerDB.get_model_breakdown_current_month(user.email)
    total = sum(r["cost_eur"] for r in rows)
    now = datetime.datetime.utcnow()

    models = [
        {
            "model": r["model"],
            "cost": round(r["cost_eur"], 4),
            "tokens": r["tokens"],
            "pct": round(r["cost_eur"] / total * 100) if total > 0 else 0,
        }
        for r in rows
    ]

    return {"models": models, "total": round(total, 4), "month": now.strftime("%B %Y")}


class ExchangeRateEntry(BaseModel):
    usd_per_eur: float  # USD per 1 EUR (ECB D.USD.EUR.SP00.A series)
    from_: int = Field(alias="from")  # unix epoch
    to: int             # unix epoch

    model_config = ConfigDict(populate_by_name=True)


class MyUsageResponse(BaseModel):
    month: int
    year: int
    total_tokens: int
    total_cost_usd: float
    total_cost_eur: float
    exchange_rates: list[ExchangeRateEntry] = []
    ledger_ready: bool = True
    credits_balance: int = 0
    credits_used: int = 0
    credits_remaining: int = 0
    credits_per_eur_cent: float = 0.0  # 0 = credits not applicable (internal)


@router.get("/my-usage", response_model=MyUsageResponse)
async def get_my_usage(user=Depends(get_verified_user)):
    """Current user's token and cost for the current calendar month, from the ledger."""
    require_billing_enabled()

    from open_webui.models.usage_ledger import UsageLedgerDB
    from open_webui.models.user_credits import UserCreditsDB, eur_to_credits

    cost_eur = UsageLedgerDB.get_cost_eur_for_user_current_month(user.email)
    cost_usd = UsageLedgerDB.get_cost_usd_for_user_current_month(user.email)
    total_tokens = UsageLedgerDB.get_tokens_for_user_current_month(user.email)
    rates = UsageLedgerDB.get_exchange_rates_current_month(user.email)
    now = datetime.datetime.utcnow()

    record = StripeBillings.get_by_user_id(user.id)
    credits_balance = credits_used = credits_remaining = 0
    credits_per_eur_cent = 0.0

    if record and record.plan_tier in CREDITS_TIERS:
        from open_webui.models.user_credits import CREDITS_PER_EUR_CENT
        row = UserCreditsDB.get(user.email)
        rate = row.credits_per_eur_cent if row else CREDITS_PER_EUR_CENT
        balance = row.balance if row else PLAN_CREDITS.get(record.plan_tier, 0)
        credits_per_eur_cent = rate
        credits_balance = balance
        credits_used = eur_to_credits(cost_eur, rate)
        credits_remaining = max(0, balance - credits_used)

    return MyUsageResponse(
        month=now.month,
        year=now.year,
        total_tokens=total_tokens,
        total_cost_usd=round(cost_usd, 6),
        total_cost_eur=round(cost_eur, 4),
        exchange_rates=[ExchangeRateEntry(**{"from": r["from"], "to": r["to"], "usd_per_eur": r["usd_per_eur"]}) for r in rates],
        ledger_ready=UsageLedgerDB.is_ledger_ready(),
        credits_balance=credits_balance,
        credits_used=credits_used,
        credits_remaining=credits_remaining,
        credits_per_eur_cent=credits_per_eur_cent,
    )
