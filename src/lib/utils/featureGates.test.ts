import { describe, it, expect } from 'vitest';
import { shouldShowTrustmiderFeedback } from './featureGates';

describe('shouldShowTrustmiderFeedback', () => {
	it('is false when the flag is off, even if a user is present', () => {
		expect(
			shouldShowTrustmiderFeedback({ features: { enable_trustmider_feedback: false } }, { id: '1' })
		).toBe(false);
	});

	it('is false when the flag is on but there is no user', () => {
		expect(
			shouldShowTrustmiderFeedback({ features: { enable_trustmider_feedback: true } }, null)
		).toBe(false);
	});

	it('is true when the flag is on and a user is present', () => {
		expect(
			shouldShowTrustmiderFeedback({ features: { enable_trustmider_feedback: true } }, { id: '1' })
		).toBe(true);
	});

	it('is false when config is undefined', () => {
		expect(shouldShowTrustmiderFeedback(undefined, { id: '1' })).toBe(false);
	});

	it('is false when features is missing from config', () => {
		expect(shouldShowTrustmiderFeedback({}, { id: '1' })).toBe(false);
	});
});
