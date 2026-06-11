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

	it('Free Trial has no postLoginRedirect', () => {
		const plan = plans.find((p) => p.name === 'Free Trial');
		expect(plan?.postLoginRedirect).toBeUndefined();
	});

	it('Premium redirects to /billing after login', () => {
		const plan = plans.find((p) => p.name === 'Premium');
		expect(plan?.postLoginRedirect).toBe('/billing');
	});

	it('Team redirects to /billing after login', () => {
		const plan = plans.find((p) => p.name === 'Business');
		expect(plan?.postLoginRedirect).toBe('/billing');
	});
});
