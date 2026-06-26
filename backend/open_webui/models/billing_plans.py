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
# trial is omitted here — it's computed at onboard time from CREDITS_PER_EUR_CENT.
PLAN_CREDITS: dict[str, int] = {
    PLAN_TIER_PRO:     1300,
    PLAN_TIER_PREMIUM: 3800,
}
