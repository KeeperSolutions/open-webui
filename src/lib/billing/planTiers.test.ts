// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { PLAN_TIER, CREDITS_TIERS, isCreditsUser, isInternalUser } from './planTiers';

describe('isCreditsUser()', () => {
	it('returns true for trial', () => expect(isCreditsUser(PLAN_TIER.TRIAL)).toBe(true));
	it('returns true for pro', () => expect(isCreditsUser(PLAN_TIER.PRO)).toBe(true));
	it('returns true for premium', () => expect(isCreditsUser(PLAN_TIER.PREMIUM)).toBe(true));
	it('returns false for internal', () => expect(isCreditsUser(PLAN_TIER.INTERNAL)).toBe(false));
	it('returns false for team', () => expect(isCreditsUser(PLAN_TIER.TEAM)).toBe(false));
	it('returns false for null', () => expect(isCreditsUser(null)).toBe(false));
	it('returns false for undefined', () => expect(isCreditsUser(undefined)).toBe(false));
	it('returns false for legacy "paid"', () => expect(isCreditsUser('paid' as any)).toBe(false));
});

describe('isInternalUser()', () => {
	it('returns true for internal', () => expect(isInternalUser(PLAN_TIER.INTERNAL)).toBe(true));
	it('returns false for trial', () => expect(isInternalUser(PLAN_TIER.TRIAL)).toBe(false));
	it('returns false for pro', () => expect(isInternalUser(PLAN_TIER.PRO)).toBe(false));
	it('returns false for null', () => expect(isInternalUser(null)).toBe(false));
	it('returns false for undefined', () => expect(isInternalUser(undefined)).toBe(false));
});

describe('CREDITS_TIERS does not contain legacy "paid"', () => {
	it('"paid" is not a credits tier', () => expect(CREDITS_TIERS.has('paid' as any)).toBe(false));
});
