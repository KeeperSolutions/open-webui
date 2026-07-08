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
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    TRIAL_CREDIT_EUR,
    UNLIMITED_USER_EMAILS,
)
from open_webui.internal.db import get_db
from open_webui.models.billing import StripeBillings, TeamInvites, TeamMembers, Teams
from open_webui.models.billing_plans import (
    CREDITS_TIERS,
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
    """Map a Stripe price ID to a plan tier string via stripe_packages DB. Defaults to pro."""
    from open_webui.models.stripe_packages import StripePackages
    pkg = StripePackages.get_by_price_id(price_id)
    return pkg.plan_tier if pkg else PLAN_TIER_PRO


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


def has_unlimited_access(email: str) -> bool:
    if not email:
        return False
    email = email.lower()
    domain = email.split("@")[-1]
    if domain in INTERNAL_EMAIL_DOMAINS:
        return True
    return email in UNLIMITED_USER_EMAILS


def is_internal_plan(record) -> bool:
    """Returns True if the billing record indicates an unlimited/internal user."""
    return record is not None and record.plan_tier == PLAN_TIER_INTERNAL


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
    Reads from credit_balances (subscription_credits + topup_credits = total allowance).
    Skips the check if no credit_balances row exists yet (race on first login).
    """
    try:
        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import CREDITS_PER_EUR_CENT, eur_to_credits

        record = StripeBillings.get_by_user_id(user_id)
        if not record or record.plan_tier not in CREDITS_TIERS:
            return

        bal = CreditBalances.get("user", email)
        if not bal:
            # No credit_balances row yet (race on first login) — skip check
            return
        total_credits = bal.subscription_credits + bal.topup_credits
        rate = bal.credits_per_eur_cent

        if record.plan_tier == PLAN_TIER_TRIAL:
            cost_eur = _get_user_alltime_cost(email, record.created_at or 0)
        else:
            cost_eur = _get_user_current_month_cost(email)

        used = eur_to_credits(cost_eur, rate)
        if used >= total_credits:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="credits_exhausted",
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("[billing] credits exhaustion check failed for %s: %s", email, e)


def _check_team_credits_exhausted(team_id: str, member: bool = False) -> None:
    """Raise 402 if the team has consumed all its credits (subscription + topup)."""
    try:
        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import CREDITS_PER_EUR_CENT, eur_to_credits

        bal = CreditBalances.get("team", team_id)
        if not bal:
            return  # No balance row yet — allow through (will be set on next renewal)

        total_credits = bal.subscription_credits + bal.topup_credits
        if total_credits <= 0:
            return  # Not configured — allow through

        used_eur = _get_team_current_month_cost(team_id)
        used_credits = eur_to_credits(used_eur, bal.credits_per_eur_cent)
        if used_credits >= total_credits:
            detail = (
                "Your team's usage credits have been exhausted. "
                "Ask your team owner to buy more at /billing."
            ) if member else (
                f"Team usage credits exhausted (used {used_credits}/{total_credits}). "
                "Buy more at /billing."
            )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=detail,
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("[billing] team credits exhaustion check failed for team_id=%s: %s", team_id, e)


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
    if has_unlimited_access(user.email) and not billing_test_mode:
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
        _check_team_credits_exhausted(team.id)
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
        _check_team_credits_exhausted(team.id, member=True)
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

    if has_unlimited_access(user.email):
        StripeBillings.upsert(
            user_id=user.id,
            plan_tier=PLAN_TIER_INTERNAL,
        )
        log.info(f"[billing] Unlimited user onboarded: {user.email}")
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
        # Assign trial credits to credit_balances at current global rate
        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import CREDITS_PER_EUR_CENT
        from open_webui.models.billing_plans import get_trial_credits
        trial_credits = get_trial_credits(CREDITS_PER_EUR_CENT)
        CreditBalances.upsert_trial(
            owner_id=user.email,
            credits=trial_credits,
            credits_per_eur_cent=CREDITS_PER_EUR_CENT,
        )
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

    # Credits (all paid tiers)
    subscription_credits: int = 0
    topup_credits: int = 0
    credits_remaining: int = 0
    credits_per_eur_cent: float = 0.0

    # Team member view
    team_owner_name: Optional[str] = None

    # Current month usage (all tiers — individual for members)
    current_month_cost_eur: float = 0.0

    is_configured: bool = False


class CheckoutResponse(BaseModel):
    url: str


class CheckoutRequest(BaseModel):
    plan_tier: str = PLAN_TIER_PRO


class PortalResponse(BaseModel):
    url: str


class AvailablePlanResponse(BaseModel):
    id: str
    name: str
    plan_tier: str
    price_eur: float
    credits: int
    seat_count: Optional[int] = None


# ---------- Endpoints ----------


@router.get("/plans", response_model=list[AvailablePlanResponse])
async def get_available_plans(user=Depends(get_verified_user)):
    """Return all active purchasable plans from the database."""
    require_billing_enabled()
    from open_webui.models.stripe_packages import StripePackages
    packages = StripePackages.get_all()
    return [
        AvailablePlanResponse(
            id=pkg.id,
            name=pkg.name,
            plan_tier=pkg.plan_tier,
            price_eur=pkg.price_eur,
            credits=pkg.credits,
            seat_count=pkg.seat_count,
        )
        for pkg in packages
    ]


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

    if record.plan_tier == PLAN_TIER_INTERNAL or has_unlimited_access(user.email):
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

        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import eur_to_credits, CREDITS_PER_EUR_CENT

        bal = CreditBalances.get("team", team.id)
        sub_credits = bal.subscription_credits if bal else 0
        topup_cred = bal.topup_credits if bal else 0
        rate = bal.credits_per_eur_cent if bal else CREDITS_PER_EUR_CENT
        credits_rem = max(0, sub_credits + topup_cred - eur_to_credits(team_month_cost, rate))

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
            subscription_credits=sub_credits,
            topup_credits=topup_cred,
            credits_remaining=credits_rem,
            credits_per_eur_cent=rate,
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
        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import eur_to_credits, CREDITS_PER_EUR_CENT

        owner = UsersModel.get_user_by_id(team.owner_user_id)
        team_month_cost = _get_team_current_month_cost(team.id)
        bal = CreditBalances.get("team", team.id)
        sub_credits = bal.subscription_credits if bal else 0
        topup_cred = bal.topup_credits if bal else 0
        rate = bal.credits_per_eur_cent if bal else CREDITS_PER_EUR_CENT
        credits_rem = max(0, sub_credits + topup_cred - eur_to_credits(team_month_cost, rate))
        return BillingStatusResponse(
            enabled=True,
            plan_tier=PLAN_TIER_TEAM_MEMBER,
            is_configured=True,
            subscription_status=team.subscription_status,
            team_id=team.id,
            team_name=team.name,
            team_owner_name=owner.name if owner else None,
            subscription_credits=sub_credits,
            topup_credits=topup_cred,
            credits_remaining=credits_rem,
            credits_per_eur_cent=rate,
            current_month_cost_eur=current_month_cost,
        )

    if record.plan_tier in (PLAN_TIER_PRO, PLAN_TIER_PREMIUM):
        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import eur_to_credits, CREDITS_PER_EUR_CENT

        upcoming_eur: Optional[float] = None
        if record.stripe_subscription_id and record.stripe_customer_id:
            try:
                client = get_stripe_client()
                invoice = client.v1.invoices.create_preview(
                    params={"customer": record.stripe_customer_id, "subscription": record.stripe_subscription_id}
                )
                upcoming_eur = invoice.amount_due / 100
            except stripe.StripeError:
                pass

        bal = CreditBalances.get("user", user.email)
        sub_credits = bal.subscription_credits if bal else 0
        topup_cred = bal.topup_credits if bal else 0
        rate = bal.credits_per_eur_cent if bal else CREDITS_PER_EUR_CENT
        credits_rem = max(0, sub_credits + topup_cred - eur_to_credits(current_month_cost, rate))

        return BillingStatusResponse(
            enabled=True,
            plan_tier=record.plan_tier,
            is_configured=True,
            subscription_status=record.subscription_status,
            upcoming_invoice_eur=upcoming_eur,
            subscription_credits=sub_credits,
            topup_credits=topup_cred,
            credits_remaining=credits_rem,
            credits_per_eur_cent=rate,
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
async def create_checkout_session(
    request: Request,
    payload: CheckoutRequest = CheckoutRequest(),
    user=Depends(get_verified_user),
):
    """Create a Stripe Checkout Session for a subscription plan."""
    require_billing_enabled()

    from open_webui.models.stripe_packages import StripePackages

    pkg = StripePackages.get_by_tier(payload.plan_tier)
    if not pkg or not pkg.stripe_price_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No active package configured for plan tier '{payload.plan_tier}'.",
        )
    price_id = pkg.stripe_price_id

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

    # Zero out any trial credit balance so it isn't applied to the first invoice.
    # Trial credit is stored as a negative customer balance (credit); debit it back to 0.
    try:
        customer = client.v1.customers.retrieve(customer_id)
        balance = getattr(customer, "balance", 0) or 0
        if balance < 0:
            client.v1.customers.balance_transactions.create(
                customer_id,
                params={
                    "amount": -balance,  # positive amount = debit, brings balance to 0
                    "currency": "eur",
                    "description": "Trial credit forfeited on subscription upgrade",
                },
            )
            log.info(f"[billing] Zeroed trial credit balance for customer {customer_id} (was {balance} cents)")
    except stripe.StripeError as e:
        log.warning(f"[billing] Could not zero customer balance for {customer_id}: {e}")

    def _create_session(cid: str) -> object:
        return client.v1.checkout.sessions.create(
            params={
                "customer": cid,
                "mode": "subscription",
                "line_items": [{"price": price_id, "quantity": 1}],
                "success_url": f"{webui_url}/billing?checkout=success",
                "cancel_url": f"{webui_url}/billing?checkout=canceled",
                "metadata": {"user_id": user.id, "price_id": price_id},
            }
        )

    try:
        session = _create_session(customer_id)
    except stripe.StripeError as e:
        error_str = str(e)
        if "No such customer" in error_str:
            # Customer exists in DB but not in this Stripe environment (e.g. test vs live)
            log.warning(f"[billing] Customer {customer_id} not found in Stripe, creating a new one.")
            try:
                customer_id = _create_customer()
                session = _create_session(customer_id)
            except stripe.StripeError as e2:
                log.error(f"Stripe checkout session create error after customer retry: {e2}")
                raise HTTPException(status_code=502, detail="Failed to create checkout session.")
        else:
            log.error(f"Stripe checkout session create error: {e}")
            raise HTTPException(status_code=502, detail="Failed to create checkout session.")

    StripeBillings.upsert(
        user_id=user.id,
        stripe_customer_id=customer_id,
        plan_tier=record.plan_tier if record else PLAN_TIER_TRIAL,
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


@router.post("/portal/update-plan", response_model=PortalResponse)
async def billing_portal_update_plan(request: Request, user=Depends(get_verified_user)):
    """Create a portal session that lands directly on the subscription update (plan change) screen."""
    require_billing_enabled()
    client = get_stripe_client()

    record = StripeBillings.get_by_user_id(user.id)
    if not record or not record.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account found.",
        )
    if not record.stripe_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active subscription found.",
        )

    webui_url = (request.app.state.WEBUI_URL or str(request.base_url)).rstrip("/")
    try:
        session = client.v1.billing_portal.sessions.create(
            params={
                "customer": record.stripe_customer_id,
                "return_url": f"{webui_url}/billing",
                "flow_data": {
                    "type": "subscription_update",
                    "subscription_update": {
                        "subscription": record.stripe_subscription_id,
                    },
                },
            }
        )
    except stripe.StripeError as e:
        log.error(f"Stripe portal update-plan session error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create billing portal session.")

    return PortalResponse(url=session.url)


def _revert_team_members_to_trial(stripe_customer_id: str, subscription_id: Optional[str] = None) -> None:
    """Revert all members of a team to trial when the team subscription is canceled.

    When subscription_id is provided the revert is skipped if the team's recorded
    subscription doesn't match — prevents misrouting when a single Stripe customer
    is shared between individual and team subscriptions.
    """
    team = Teams.get_by_customer_id(stripe_customer_id)
    if not team:
        return
    if subscription_id and team.stripe_subscription_id and team.stripe_subscription_id != subscription_id:
        log.info("[billing] Skipping team revert: sub_id mismatch (team=%s expected=%s got=%s)",
                 team.id, team.stripe_subscription_id, subscription_id)
        return
    _revert_team_members_to_trial_by_team_id(team.id)


def _revert_team_members_to_trial_by_team_id(team_id: str) -> None:
    """Revert all non-owner members of the given team to trial."""
    members = TeamMembers.get_by_team_id(team_id)
    for m in members:
        if m.role != "owner":
            StripeBillings.revert_to_trial(m.user_id)
    log.info("[billing] Reverted %d team members to trial for team %s", len(members), team_id)


def _resolve_team_and_billing(
    customer_id: Optional[str],
    subscription_id: Optional[str] = None,
):
    """Return (team, billing_record) for a Stripe event.

    Handles both the new single-customer model (team owner reuses their personal
    Stripe customer) and the legacy separate-customer model (team has its own
    Stripe customer).

    When subscription_id is supplied the team is only returned when its
    stripe_subscription_id matches AND its subscription_status is active/trialing —
    this prevents individual-plan events from being mis-routed to the team path on a
    shared customer.
    """
    if not customer_id:
        return None, None

    record = StripeBillings.get_by_customer_id(customer_id)
    if record:
        # New model: personal customer shared with team subscription
        team = Teams.get_by_owner_user_id(record.user_id)
        if team and team.subscription_status in ("active", "trialing"):
            if subscription_id is None or team.stripe_subscription_id == subscription_id:
                return team, record
        return None, record

    # Legacy model: team has its own Stripe customer
    team = Teams.get_by_customer_id(customer_id)
    return team, None


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
    from open_webui.models.stripe_packages import StripePackages
    return [
        {"seat_count": p.seat_count, "price_eur": p.price_eur}
        for p in StripePackages.get_all_by_tier(PLAN_TIER_TEAM)
        if p.seat_count
    ]


@router.post("/team/create", response_model=CheckoutResponse)
async def create_team(body: TeamCreateRequest, request: Request, user=Depends(get_verified_user)):
    """Create a team and return a Stripe checkout or redirect URL.

    Uses the owner's existing personal Stripe customer (single-customer model) so
    that all subscriptions — individual and team — live on one customer, enabling
    automatic proration when switching plans via the Stripe portal.

    - Trial users: redirected to a Stripe Checkout session.
    - Pro/Premium users: subscription is updated in-place via the Stripe API
      (no new checkout); proration is applied automatically. The
      customer.subscription.updated webhook activates the team.
    """
    require_billing_enabled()

    seat_count = body.seat_count
    from open_webui.models.stripe_packages import StripePackages
    team_plans = StripePackages.get_all_by_tier(PLAN_TIER_TEAM)
    tier_config = next((p for p in team_plans if p.seat_count == seat_count), None)
    if not tier_config:
        available = sorted({p.seat_count for p in team_plans if p.seat_count})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid seat count. Available options: {available}",
        )

    # Prevent creating multiple active teams
    existing_team = Teams.get_by_owner_user_id(user.id)
    if existing_team and existing_team.subscription_status in ("active", "trialing"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have an active team. Manage it at /billing.",
        )

    # Reuse the owner's personal Stripe customer — single customer for all plans
    record = StripeBillings.get_by_user_id(user.id)
    if not record or not record.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account found. Please complete account setup first.",
        )
    customer_id = record.stripe_customer_id
    team_price_id = tier_config.stripe_price_id

    client = get_stripe_client()
    webui_url = (request.app.state.WEBUI_URL or str(request.base_url)).rstrip("/")

    # Persist team record first — safe to do before Stripe; if Stripe fails the
    # record stays with subscription_status=None and will be reused on retry.
    if existing_team:
        # Re-use the incomplete team (no active subscription)
        team = existing_team
        Teams.update(team.id, stripe_customer_id=customer_id)
    else:
        try:
            team = Teams.create(
                name=body.name,
                owner_user_id=user.id,
                seat_limit=seat_count,
            )
            Teams.update(team.id, stripe_customer_id=customer_id)
            TeamMembers.add(team.id, user.id, role="owner")
        except Exception as e:
            log.error(f"[billing] Team DB create failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to create team record.")

    # ── Trial / no subscription → new Stripe Checkout session ────────────
    if record.plan_tier == PLAN_TIER_TRIAL or not record.stripe_subscription_id:
        try:
            session = client.v1.checkout.sessions.create(
                params={
                    "customer": customer_id,
                    "mode": "subscription",
                    "line_items": [{"price": team_price_id, "quantity": 1}],
                    "success_url": f"{webui_url}/billing?checkout=success",
                    "cancel_url": f"{webui_url}/billing?checkout=canceled",
                    "metadata": {"user_id": user.id, "price_id": team_price_id},
                }
            )
        except stripe.StripeError as e:
            log.error(f"[billing] Team checkout session error: {e}")
            raise HTTPException(status_code=502, detail="Failed to create checkout session.")
        return CheckoutResponse(url=session.url)

    # ── Pro / Premium → update existing subscription in-place ────────────
    # The customer.subscription.updated webhook fires and activates the team.
    try:
        sub = client.v1.subscriptions.retrieve(record.stripe_subscription_id)
        item_id = sub.items.data[0].id
        client.v1.subscriptions.update(
            record.stripe_subscription_id,
            params={
                "items": [{"id": item_id, "price": team_price_id}],
                "proration_behavior": "create_prorations",
                "metadata": {"user_id": user.id, "price_id": team_price_id},
            },
        )
    except stripe.StripeError as e:
        log.error(f"[billing] Team subscription update error: {e}")
        raise HTTPException(status_code=502, detail="Failed to update subscription to team plan.")

    log.info("[billing] Team subscription update initiated: user=%s seats=%d", user.id, seat_count)
    return CheckoutResponse(url=f"{webui_url}/billing?checkout=success")


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


class TeamUpdateNameRequest(BaseModel):
    name: str


@router.patch("/team/name")
async def update_team_name(body: TeamUpdateNameRequest, user=Depends(get_verified_user)):
    """Update the team name. Only the team owner may call this."""
    require_billing_enabled()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Team name cannot be empty.")
    team = Teams.get_by_owner_user_id(user.id)
    if not team:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own a team.")
    updated = Teams.update(team.id, name=name)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update team name.")
    return {"name": updated.name}


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


@router.post("/team/portal/update-plan", response_model=PortalResponse)
async def team_billing_portal_update_plan(request: Request, user=Depends(get_verified_user)):
    """Stripe portal landing directly on the team subscription update screen (seat tier change)."""
    require_billing_enabled()

    team = Teams.get_by_owner_user_id(user.id)
    if not team or not team.stripe_customer_id:
        raise HTTPException(status_code=404, detail="No team billing account found.")
    if not team.stripe_subscription_id:
        raise HTTPException(status_code=400, detail="No active team subscription found.")

    client = get_stripe_client()
    webui_url = (request.app.state.WEBUI_URL or str(request.base_url)).rstrip("/")
    try:
        session = client.v1.billing_portal.sessions.create(
            params={
                "customer": team.stripe_customer_id,
                "return_url": f"{webui_url}/billing",
                "flow_data": {
                    "type": "subscription_update",
                    "subscription_update": {
                        "subscription": team.stripe_subscription_id,
                    },
                },
            }
        )
    except stripe.StripeError as e:
        log.error(f"[billing] Team portal update-plan error: {e}")
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

        # ── Resolve purchased price/package ───────────────────────────────
        # Route to team vs. individual based on the price tier, NOT by which
        # Stripe customer was used.  The single-customer model means both paths
        # share the same customer_id; only the price_id distinguishes them.
        from open_webui.models.stripe_packages import StripePackages as _SPs

        _csc_price_id = _price_id_from_session(data)

        # Fallback: fetch subscription from Stripe when price_id absent from metadata
        if not _csc_price_id and subscription_id:
            try:
                _c = get_stripe_client()
                _sub_data = _c.v1.subscriptions.retrieve(subscription_id)
                _itms = getattr(_sub_data, "items", None)
                _idata = getattr(_itms, "data", None) or []
                if _idata:
                    _pr = getattr(_idata[0], "price", None)
                    _pid = getattr(_pr, "id", None)
                    if _pid:
                        _csc_price_id = _pid
                        log.info("[billing] checkout.session: resolved price_id=%s via subscription fetch", _csc_price_id)
            except Exception as _fe:
                log.warning("[billing] checkout.session: could not fetch subscription for price fallback: %s", _fe)

        _csc_pkg = _SPs.get_by_price_id(_csc_price_id) if _csc_price_id else None
        _csc_tier = _csc_pkg.plan_tier if _csc_pkg else None

        # ── Team checkout ──────────────────────────────────────────────────
        if _csc_tier == PLAN_TIER_TEAM:
            from open_webui.models.credit_balances import CreditBalances
            from open_webui.models.purchase_history import PurchaseHistory

            # New single-customer model: find team via billing record → owner
            billing_rec = StripeBillings.get_by_customer_id(customer_id) if customer_id else None
            team = Teams.get_by_owner_user_id(billing_rec.user_id) if billing_rec else None

            # Legacy fallback: team with its own separate Stripe customer
            if team is None:
                team = Teams.get_by_customer_id(customer_id) if customer_id else None
                billing_rec = None  # no shared StripeBillings record for legacy separate customer

            if team:
                pkg = _csc_pkg
                credits = pkg.credits if pkg else 0
                plan_tier = pkg.plan_tier if pkg else PLAN_TIER_TEAM
                owner_user_id = team.owner_user_id

                Teams.update(
                    team.id,
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    subscription_status="active",
                    monthly_credits=credits,
                )
                StripeBillings.upsert(
                    user_id=owner_user_id,
                    stripe_subscription_id=subscription_id,
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
                # Zero out the owner's personal credit balance — they are now
                # on a team plan and must use team credits exclusively.
                from open_webui.models.users import Users as _CscUsers
                _csc_owner = _CscUsers.get_user_by_id(owner_user_id)
                if _csc_owner:
                    CreditBalances.reset_all("user", _csc_owner.email)
                PurchaseHistory.insert(
                    user_id=owner_user_id,
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
                log.warning("[billing] Team checkout: no team found for customer_id=%s price_id=%s", customer_id, _csc_price_id)

        else:
            # ── Individual user subscription checkout ─────────────────────
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
                credits = pkg.credits if pkg else 0

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
                # Only set credits if we resolved a valid package — avoids overwriting
                # credits already set by a concurrent invoice.paid webhook when
                # price_id lookup fails (credits would be 0 and would wipe correct balance).
                if credits > 0:
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
            sub_id_evt = getattr(data, "id", None)  # subscription ID from this event

            # ── Status update — route with subscription_id guard ──────────
            # With the single-customer model the same customer_id is used for both
            # individual and team subscriptions, so we must match by subscription_id.
            rec_su = StripeBillings.get_by_customer_id(customer_id)
            if rec_su:
                # New model: personal customer shared with team
                team_su = Teams.get_by_owner_user_id(rec_su.user_id)
                if team_su and (not team_su.stripe_subscription_id or team_su.stripe_subscription_id == sub_id_evt):
                    Teams.update(team_su.id, subscription_status=new_status)
                StripeBillings.update_subscription_status(customer_id, new_status)
            else:
                # Legacy model: separate team customer
                if not Teams.update_subscription_status(customer_id, new_status):
                    StripeBillings.update_subscription_status(customer_id, new_status)

            # ── Plan upgrade/downgrade detection ───────────────────────────
            # Read the current price from the subscription items to detect plan changes.
            if event_type == "customer.subscription.updated" and new_status == "active":
                from open_webui.models.credit_balances import CreditBalances
                from open_webui.models.stripe_packages import StripePackages
                from open_webui.models.users import Users as _Users
                from open_webui.models.user_credits import CREDITS_PER_EUR_CENT

                try:
                    items = getattr(data, "items", None)
                    item_data = getattr(items, "data", None) or (items.get("data", []) if items else [])
                    new_price_id = None
                    if item_data:
                        price = getattr(item_data[0], "price", None) or item_data[0].get("price", {})
                        new_price_id = getattr(price, "id", None) or price.get("id", "")

                    if new_price_id:
                        pkg = StripePackages.get_by_price_id(new_price_id)

                        # ── Switched to a team plan ─────────────────────────
                        if pkg and pkg.plan_tier == PLAN_TIER_TEAM:
                            rec = StripeBillings.get_by_customer_id(customer_id)
                            if rec:
                                team = Teams.get_by_owner_user_id(rec.user_id) or Teams.get_by_customer_id(customer_id)
                                if not team:
                                    # No team record — user upgraded via the Stripe billing portal
                                    # without going through the in-app create_team API. Create the
                                    # team record now so this webhook can activate it.
                                    _portal_owner = _Users.get_user_by_id(rec.user_id)
                                    _portal_name = (
                                        f"{_portal_owner.name}'s Team"
                                        if _portal_owner and _portal_owner.name
                                        else "My Team"
                                    )
                                    team = Teams.create(
                                        name=_portal_name,
                                        owner_user_id=rec.user_id,
                                        seat_limit=pkg.seat_count or 5,
                                    )
                                    Teams.update(team.id, stripe_customer_id=customer_id)
                                    TeamMembers.add(team.id, rec.user_id, role="owner")
                                    log.info(
                                        "[billing] Auto-created team on portal upgrade: team=%s user=%s",
                                        team.id, rec.user_id,
                                    )
                                if team:
                                    seat_limit_new = pkg.seat_count if pkg.seat_count else team.seat_limit
                                    Teams.update(
                                        team.id,
                                        stripe_subscription_id=sub_id_evt,
                                        subscription_status="active",
                                        monthly_credits=pkg.credits,
                                        seat_limit=seat_limit_new,
                                    )
                                    StripeBillings.upsert(
                                        user_id=rec.user_id,
                                        stripe_subscription_id=sub_id_evt,
                                        plan_tier=PLAN_TIER_TEAM,
                                        subscription_status="active",
                                        team_id=team.id,
                                    )
                                    existing_bal = CreditBalances.get("team", team.id)
                                    rate = existing_bal.credits_per_eur_cent if existing_bal else CREDITS_PER_EUR_CENT
                                    CreditBalances.set_subscription(
                                        "team", team.id, pkg.credits, rate, int(_time.time())
                                    )
                                    # Zero out the owner's personal credit balance — they are now
                                    # on a team plan and must use team credits exclusively.
                                    _owner_u = _Users.get_user_by_id(rec.user_id)
                                    if _owner_u:
                                        CreditBalances.reset_all("user", _owner_u.email)
                                    from open_webui.models.purchase_history import PurchaseHistory
                                    PurchaseHistory.insert(
                                        user_id=rec.user_id,
                                        event_type="subscription_start",
                                        team_id=team.id,
                                        stripe_customer_id=customer_id,
                                        stripe_subscription_id=sub_id_evt,
                                        plan_tier=pkg.plan_tier,
                                        package_id=pkg.id,
                                        subscription_credits_granted=pkg.credits,
                                        amount_eur=pkg.price_eur,
                                    )
                                    log.info(
                                        "[billing] Team subscription synced: team=%s plan=%s credits=%d seats=%d",
                                        team.id, pkg.plan_tier, pkg.credits, seat_limit_new,
                                    )
                                else:
                                    log.warning("[billing] subscription.updated team: no team found for user=%s", rec.user_id)

                        # ── Switched to an individual plan ──────────────────
                        elif pkg and pkg.plan_tier in (PLAN_TIER_PRO, PLAN_TIER_PREMIUM):
                            rec = StripeBillings.get_by_customer_id(customer_id)
                            if rec:
                                was_team = rec.plan_tier == PLAN_TIER_TEAM
                                StripeBillings.upsert(
                                    user_id=rec.user_id,
                                    plan_tier=pkg.plan_tier,
                                    subscription_status="active",
                                    free_tier_credit_applied=rec.free_tier_credit_applied,
                                )
                                u = _Users.get_user_by_id(rec.user_id)
                                if u:
                                    existing_bal = CreditBalances.get("user", u.email)
                                    rate = existing_bal.credits_per_eur_cent if existing_bal else CREDITS_PER_EUR_CENT
                                    CreditBalances.set_subscription(
                                        "user", u.email, pkg.credits, rate, int(_time.time())
                                    )
                                    log.info(
                                        "[billing] Subscription synced: user=%s plan=%s credits=%d",
                                        u.email, pkg.plan_tier, pkg.credits,
                                    )
                                if was_team:
                                    # Owner downgraded from team to individual — dissolve team members
                                    team = Teams.get_by_owner_user_id(rec.user_id) or Teams.get_by_customer_id(customer_id)
                                    if team:
                                        _revert_team_members_to_trial_by_team_id(team.id)
                                        Teams.update(team.id, subscription_status="canceled")
                                        log.info("[billing] Team dissolved on individual downgrade: team=%s", team.id)

                except Exception as _e:
                    log.warning("[billing] Plan-change detection failed: %s", _e)

            cancel_at_period_end = getattr(data, "cancel_at_period_end", False)

            # ── Scheduled cancellation (cancel at period end) ──────────────
            if (
                event_type == "customer.subscription.updated"
                and cancel_at_period_end
                and new_status == "active"
            ):
                from open_webui.models.purchase_history import PurchaseHistory

                cancel_at = getattr(data, "cancel_at", None)
                team_sc, record_sc = _resolve_team_and_billing(customer_id, sub_id_evt)
                if team_sc:
                    PurchaseHistory.insert(
                        user_id=team_sc.owner_user_id,
                        event_type="cancellation_scheduled",
                        team_id=team_sc.id,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=sub_id_evt,
                    )
                elif record_sc:
                    PurchaseHistory.insert(
                        user_id=record_sc.user_id,
                        event_type="cancellation_scheduled",
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=sub_id_evt,
                        plan_tier=record_sc.plan_tier,
                    )
                log.info(
                    "[billing] Subscription scheduled for cancellation: customer=%s cancel_at=%s",
                    customer_id, cancel_at,
                )

            if event_type == "customer.subscription.deleted" or new_status == "canceled":
                from open_webui.models.credit_balances import CreditBalances
                from open_webui.models.purchase_history import PurchaseHistory
                from open_webui.models.users import Users as _Users

                team_del, record_del = _resolve_team_and_billing(customer_id, sub_id_evt)
                if team_del:
                    CreditBalances.reset_all("team", team_del.id)
                    PurchaseHistory.insert(
                        user_id=team_del.owner_user_id,
                        event_type="cancellation",
                        team_id=team_del.id,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=sub_id_evt,
                    )
                elif record_del:
                    u_del = _Users.get_user_by_id(record_del.user_id)
                    owner_id_del = u_del.email if u_del else record_del.user_id
                    CreditBalances.reset_all("user", owner_id_del)
                    PurchaseHistory.insert(
                        user_id=record_del.user_id,
                        event_type="cancellation",
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=sub_id_evt,
                        plan_tier=record_del.plan_tier,
                    )

                _revert_team_members_to_trial(customer_id, subscription_id=sub_id_evt)
            log.info("[billing] Subscription updated: customer=%s status=%s", customer_id, new_status)

    elif event_type == "invoice.payment_failed":
        customer_id = getattr(data, "customer", None)
        if customer_id:
            sub_id_fail = getattr(data, "subscription", None)
            team_fail, record_fail = _resolve_team_and_billing(customer_id, sub_id_fail)
            if team_fail:
                Teams.update(team_fail.id, subscription_status="past_due")
            elif record_fail:
                StripeBillings.update_subscription_status(customer_id, "past_due")
            else:
                # Legacy fallback
                if not Teams.update_subscription_status(customer_id, "past_due"):
                    StripeBillings.update_subscription_status(customer_id, "past_due")
            log.warning("[billing] Payment failed: customer=%s", customer_id)

    elif event_type == "invoice.paid":
        customer_id = getattr(data, "customer", None)
        invoice_id = getattr(data, "id", None)
        billing_reason = getattr(data, "billing_reason", None)

        # subscription_create: handled by checkout.session.completed — skip to avoid duplicate.
        # subscription_update: proration invoice for mid-cycle plan changes; credits and
        #   purchase history are already written by customer.subscription.updated — skip to
        #   avoid overwriting the correct credits with the old plan's proration price_id.
        if billing_reason in ("subscription_create", "subscription_update"):
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

            # Resolve price → package from invoice line items.
            # Upgrade invoices have multiple lines (proration credits + new subscription charge).
            # Prefer the subscription line item (type="subscription") with a positive amount;
            # fall back to the first line item if none match.
            price_id = None
            try:
                lines = getattr(data, "lines", None)
                if lines:
                    line_data = getattr(lines, "data", None) or lines.get("data", [])
                    if line_data:
                        # Find the subscription line with a positive amount first
                        chosen = None
                        for item in line_data:
                            item_type = getattr(item, "type", None) or item.get("type", "")
                            item_amount = getattr(item, "amount", None) or item.get("amount", 0)
                            if item_type == "subscription" and item_amount > 0:
                                chosen = item
                                break
                        # Fall back to first subscription line, then first line overall
                        if chosen is None:
                            for item in line_data:
                                item_type = getattr(item, "type", None) or item.get("type", "")
                                if item_type == "subscription":
                                    chosen = item
                                    break
                        if chosen is None:
                            chosen = line_data[0]
                        price = getattr(chosen, "price", None) or chosen.get("price", {})
                        price_id = getattr(price, "id", None) or price.get("id", "")
            except Exception:
                pass
            pkg = StripePackages.get_by_price_id(price_id) if price_id else None
            log.info("[billing] invoice.paid price_id=%s pkg=%s", price_id, pkg.plan_tier if pkg else None)

            team_ip, record_ip = _resolve_team_and_billing(customer_id, subscription_id)
            if team_ip:
                if team_ip.subscription_status == "past_due":
                    Teams.update(team_ip.id, subscription_status="active")
                    log.info("[billing] Team payment recovered: customer=%s", customer_id)
                if pkg:
                    credits = pkg.credits
                    existing_bal = CreditBalances.get("team", team_ip.id)
                    rate = existing_bal.credits_per_eur_cent if existing_bal else CREDITS_PER_EUR_CENT
                    CreditBalances.set_subscription("team", team_ip.id, credits, rate, int(_time.time()))
                    Teams.update(team_ip.id, monthly_credits=credits)
                    PurchaseHistory.insert(
                        user_id=team_ip.owner_user_id,
                        event_type="renewal",
                        team_id=team_ip.id,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=subscription_id,
                        stripe_invoice_id=invoice_id,
                        plan_tier=pkg.plan_tier,
                        package_id=pkg.id,
                        subscription_credits_granted=credits,
                        amount_eur=amount_eur,
                    )
                    log.info("[billing] Team renewal credits reset: team_id=%s credits=%d", team_ip.id, credits)
            elif record_ip:
                if record_ip.subscription_status == "past_due":
                    StripeBillings.update_subscription_status(customer_id, "active")
                    log.info("[billing] Payment recovered: customer=%s", customer_id)
                if record_ip.plan_tier in (PLAN_TIER_PRO, PLAN_TIER_PREMIUM) or (pkg and pkg.plan_tier in (PLAN_TIER_PRO, PLAN_TIER_PREMIUM)):
                    u = _Users.get_user_by_id(record_ip.user_id)
                    if u:
                        effective_plan_tier = pkg.plan_tier if pkg else record_ip.plan_tier
                        credits = pkg.credits if pkg else 0
                        existing_bal = CreditBalances.get("user", u.email)
                        rate = existing_bal.credits_per_eur_cent if existing_bal else CREDITS_PER_EUR_CENT
                        CreditBalances.set_subscription("user", u.email, credits, rate, int(_time.time()))
                        PurchaseHistory.insert(
                            user_id=record_ip.user_id,
                            event_type="renewal",
                            stripe_customer_id=customer_id,
                            stripe_subscription_id=subscription_id,
                            stripe_invoice_id=invoice_id,
                            plan_tier=effective_plan_tier,
                            package_id=pkg.id if pkg else None,
                            subscription_credits_granted=credits,
                            amount_eur=amount_eur,
                        )
                        log.info("[billing] Monthly renewal credits reset: user=%s plan=%s credits=%d", u.email, effective_plan_tier, credits)

    elif event_type == "payment_method.attached":
        customer_id = getattr(data, "customer", None)
        pm_id = getattr(data, "id", None)
        if customer_id and pm_id:
            record = StripeBillings.get_by_customer_id(customer_id)
            if record:
                if record.plan_tier == PLAN_TIER_TEAM:
                    team = Teams.get_by_owner_user_id(record.user_id)
                    if team:
                        Teams.update(team.id, stripe_payment_method_id=pm_id)
                else:
                    StripeBillings.upsert(
                        user_id=record.user_id,
                        stripe_payment_method_id=pm_id,
                        free_tier_credit_applied=record.free_tier_credit_applied,
                    )
            else:
                # Legacy: separate team customer
                team = Teams.get_by_customer_id(customer_id)
                if team:
                    Teams.update(team.id, stripe_payment_method_id=pm_id)

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
    from open_webui.models.user_credits import eur_to_credits

    cost_eur = UsageLedgerDB.get_cost_eur_for_user_current_month(user.email)
    cost_usd = UsageLedgerDB.get_cost_usd_for_user_current_month(user.email)
    total_tokens = UsageLedgerDB.get_tokens_for_user_current_month(user.email)
    rates = UsageLedgerDB.get_exchange_rates_current_month(user.email)
    now = datetime.datetime.utcnow()

    record = StripeBillings.get_by_user_id(user.id)

    # Unlimited users (internal domains or explicit UNLIMITED_USER_EMAILS) get no credits
    if has_unlimited_access(user.email) or is_internal_plan(record):
        return MyUsageResponse(
            month=now.month,
            year=now.year,
            total_tokens=total_tokens,
            total_cost_usd=round(cost_usd, 6),
            total_cost_eur=round(cost_eur, 4),
            exchange_rates=[ExchangeRateEntry(**{"from": r["from"], "to": r["to"], "usd_per_eur": r["usd_per_eur"]}) for r in rates],
            ledger_ready=UsageLedgerDB.is_ledger_ready(),
            credits_balance=-1,
            credits_used=0,
            credits_remaining=-1,
            credits_per_eur_cent=0.0,
        )

    credits_balance = credits_used = credits_remaining = 0
    credits_per_eur_cent = 0.0

    if record and record.plan_tier in CREDITS_TIERS:
        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import CREDITS_PER_EUR_CENT
        bal = CreditBalances.get("user", user.email)
        rate = bal.credits_per_eur_cent if bal else CREDITS_PER_EUR_CENT
        balance = (bal.subscription_credits + bal.topup_credits) if bal else 0
        credits_per_eur_cent = rate
        credits_balance = balance
        # Only count usage since the subscription period started to avoid
        # trial usage counting against subscription credits.
        period_start = bal.period_start if bal else None
        usage_eur = (
            UsageLedgerDB.get_cost_eur_for_user_since(user.email, period_start)
            if period_start
            else cost_eur
        )
        credits_used = eur_to_credits(usage_eur, rate)
        credits_remaining = max(0, balance - credits_used)
    elif record and record.plan_tier == PLAN_TIER_TEAM and record.team_id:
        # Team owner: resolve rate from the team's credit balance, compute personal usage
        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import CREDITS_PER_EUR_CENT
        team = Teams.get_by_id(record.team_id)
        rate = CREDITS_PER_EUR_CENT
        if team:
            bal = CreditBalances.get("team", team.id)
            if bal:
                rate = bal.credits_per_eur_cent
        credits_per_eur_cent = rate
        credits_used = eur_to_credits(cost_eur, rate)
    elif record and record.plan_tier == PLAN_TIER_TEAM_MEMBER and record.team_id:
        # Team members: resolve the conversion rate from the team's credit balance
        from open_webui.models.credit_balances import CreditBalances
        from open_webui.models.user_credits import CREDITS_PER_EUR_CENT
        bal = CreditBalances.get("team", record.team_id)
        rate = bal.credits_per_eur_cent if bal else CREDITS_PER_EUR_CENT
        credits_per_eur_cent = rate
        credits_used = eur_to_credits(cost_eur, rate)

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
