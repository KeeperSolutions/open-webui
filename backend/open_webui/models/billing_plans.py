"""Single source of truth for plan tier strings and credit allocations.

Import these constants everywhere instead of using raw strings.
"""

PLAN_TIER_INTERNAL    = "internal"
PLAN_TIER_TRIAL       = "trial"
PLAN_TIER_PRO         = "pro"
PLAN_TIER_PREMIUM     = "premium"
PLAN_TIER_TEAM        = "team"
PLAN_TIER_TEAM_MEMBER = "team_member"

# Tiers that use the credits system (balance tracked in user_credits table)
CREDITS_TIERS: frozenset[str] = frozenset({
    PLAN_TIER_TRIAL,
    PLAN_TIER_PRO,
    PLAN_TIER_PREMIUM,
})

# Monthly credit allocations per tier.
# trial is omitted here — its value depends on the CREDITS_PER_EUR_CENT env var which
# is not available at import time. Call get_trial_credits(rate) instead of reading
# PLAN_CREDITS[PLAN_TIER_TRIAL]; user_credits.py populates the key at import as a
# convenience for legacy call sites, but new code should prefer the function.
PLAN_CREDITS: dict[str, int] = {
    PLAN_TIER_PRO:     1300,
    PLAN_TIER_PREMIUM: 3800,
}


def get_trial_credits(credits_per_eur_cent: float) -> int:
    """Return the one-time trial credit balance for a given conversion rate."""
    return round(2.00 * 100 * credits_per_eur_cent)
