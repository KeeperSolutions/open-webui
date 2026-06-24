export const PLAN_TIER = {
	INTERNAL:    'internal',
	TRIAL:       'trial',
	PRO:         'pro',
	PREMIUM:     'premium',
	TEAM:        'team',
	TEAM_MEMBER: 'team_member',
} as const;

export type PlanTier = typeof PLAN_TIER[keyof typeof PLAN_TIER];

export const CREDITS_TIERS = new Set<PlanTier>([
	PLAN_TIER.TRIAL,
	PLAN_TIER.PRO,
	PLAN_TIER.PREMIUM,
]);

export function isCreditsUser(tier: PlanTier | null | undefined): boolean {
	return !!tier && CREDITS_TIERS.has(tier as PlanTier);
}

export function isInternalUser(tier: PlanTier | null | undefined): boolean {
	return tier === PLAN_TIER.INTERNAL;
}
