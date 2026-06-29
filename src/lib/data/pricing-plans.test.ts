import { describe, it, expect } from 'vitest';
import { plans } from './pricing-plans';

describe('pricing plans data integrity', () => {
	it('every plan has required fields', () => {
		for (const plan of plans) {
			expect(plan.name).toBeTruthy();
			expect(plan.currency).toBeTruthy();
			expect(typeof plan.price).toBe('number');
			expect(Array.isArray(plan.features)).toBe(true);
			expect(plan.features.length).toBeGreaterThan(0);
			expect(plan.ctaLabel).toBeTruthy();
		}
	});

	it('has exactly 4 plans in order', () => {
		expect(plans.map((p) => p.name)).toEqual(['Free Trial', 'Pro', 'Premium', 'Business']);
	});

	it('Free Trial has no postLoginRedirect', () => {
		const plan = plans.find((p) => p.name === 'Free Trial');
		expect(plan?.postLoginRedirect).toBeUndefined();
	});

	it('Pro redirects to /billing after login', () => {
		const plan = plans.find((p) => p.name === 'Pro');
		expect(plan?.postLoginRedirect).toBe('/billing');
	});

	it('Premium redirects to /billing after login', () => {
		const plan = plans.find((p) => p.name === 'Premium');
		expect(plan?.postLoginRedirect).toBe('/billing');
	});

	it('Business redirects to /billing after login', () => {
		const plan = plans.find((p) => p.name === 'Business');
		expect(plan?.postLoginRedirect).toBe('/billing');
	});

	it('Premium is the only most-popular plan', () => {
		const popular = plans.filter((p) => p.isMostPopular);
		expect(popular).toHaveLength(1);
		expect(popular[0].name).toBe('Premium');
	});

	it('every plan has a credits badge', () => {
		for (const plan of plans) {
			expect(plan.creditsHighlight).toBeTruthy();
			expect(plan.creditsLabel).toBeTruthy();
		}
	});
});
