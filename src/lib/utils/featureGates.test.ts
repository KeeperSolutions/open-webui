import { describe, it, expect } from 'vitest';
import { shouldShowTrustminderFeedback } from './featureGates';

describe('shouldShowTrustminderFeedback', () => {
	it('is false when the flag is off, even if a user is present', () => {
		expect(
			shouldShowTrustminderFeedback({ features: { enable_trustminder_feedback: false } }, { id: '1' })
		).toBe(false);
	});

	it('is false when the flag is on but there is no user', () => {
		expect(
			shouldShowTrustminderFeedback({ features: { enable_trustminder_feedback: true } }, null)
		).toBe(false);
	});

	it('is true when the flag is on and a user is present', () => {
		expect(
			shouldShowTrustminderFeedback({ features: { enable_trustminder_feedback: true } }, { id: '1' })
		).toBe(true);
	});

	it('is false when config is undefined', () => {
		expect(shouldShowTrustminderFeedback(undefined, { id: '1' })).toBe(false);
	});

	it('is false when features is missing from config', () => {
		expect(shouldShowTrustminderFeedback({}, { id: '1' })).toBe(false);
	});
});
