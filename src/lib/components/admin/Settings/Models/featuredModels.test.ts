import { describe, it, expect } from 'vitest';
import {
	PROVIDER_NAME_MIN,
	PROVIDER_NAME_MAX,
	TAG_MAX,
	MAX_TAGS,
	isProviderNameValid,
	validateFeaturedModels,
	type FeaturedModelEntry
} from './featuredModels';

// A FEATURED_MODELS entry as the admin editor holds it. `tags` is a fixed-length
// triple of plain strings; `validateFeaturedModels` only cares about their length.
const entry = (overrides: Partial<FeaturedModelEntry> = {}): FeaturedModelEntry => ({
	model_id: 'gpt-x',
	provider_name: 'OpenAI',
	featured_name: 'Flagship',
	tags: ['fast', 'smart', ''],
	order: 0,
	...overrides
});

const REQUIRED_MSG = 'Provider name is required for every featured model.';
const LENGTH_MSG = 'Provider name must be between 3 and 24 characters.';
const TAG_MSG = 'Each tag must be 10 characters or fewer.';
const TAG_COUNT_MSG = 'At most 3 tags are allowed per featured model.';

describe('field-limit constants', () => {
	it('match the backend validator (configs.py _validate_featured_models)', () => {
		expect(PROVIDER_NAME_MIN).toBe(3);
		expect(PROVIDER_NAME_MAX).toBe(24);
		expect(TAG_MAX).toBe(10);
		expect(MAX_TAGS).toBe(3);
	});
});

describe('isProviderNameValid()', () => {
	it('rejects a 2-char name (below min)', () => {
		expect(isProviderNameValid('ab')).toBe(false);
	});

	it('accepts a 3-char name (min boundary)', () => {
		expect(isProviderNameValid('abc')).toBe(true);
	});

	it('accepts a 24-char name (max boundary)', () => {
		expect(isProviderNameValid('a'.repeat(24))).toBe(true);
	});

	it('rejects a 25-char name (above max)', () => {
		expect(isProviderNameValid('a'.repeat(25))).toBe(false);
	});

	it('rejects a whitespace-only name', () => {
		expect(isProviderNameValid('   ')).toBe(false);
	});

	it('trims leading/trailing spaces before measuring — "  abc  " is valid', () => {
		expect(isProviderNameValid('  abc  ')).toBe(true);
	});

	it('trims before measuring — " ab " (2 real chars) is invalid', () => {
		expect(isProviderNameValid(' ab ')).toBe(false);
	});

	it('counts a padded 24-char name by its trimmed length', () => {
		expect(isProviderNameValid(`  ${'a'.repeat(24)}  `)).toBe(true);
	});

	// The declared signature is (name: string); guard against null/undefined at runtime.
	it('treats null/undefined as invalid rather than throwing', () => {
		// @ts-expect-error — exercising the ?? '' runtime guard
		expect(isProviderNameValid(null)).toBe(false);
		// @ts-expect-error — exercising the ?? '' runtime guard
		expect(isProviderNameValid(undefined)).toBe(false);
	});
});

describe('validateFeaturedModels()', () => {
	it('returns null for an empty list', () => {
		expect(validateFeaturedModels([])).toBeNull();
	});

	it('returns null for a fully valid list', () => {
		expect(
			validateFeaturedModels([entry(), entry({ model_id: 'b', provider_name: 'Anthropic' })])
		).toBeNull();
	});

	it('returns the "required" message for a blank provider name', () => {
		expect(validateFeaturedModels([entry({ provider_name: '' })])).toBe(REQUIRED_MSG);
	});

	it('returns the "required" message for a whitespace-only provider name', () => {
		expect(validateFeaturedModels([entry({ provider_name: '   ' })])).toBe(REQUIRED_MSG);
	});

	it('returns the length message for a 2-char provider name', () => {
		expect(validateFeaturedModels([entry({ provider_name: 'ab' })])).toBe(LENGTH_MSG);
	});

	it('returns the length message for a 25-char provider name', () => {
		expect(validateFeaturedModels([entry({ provider_name: 'a'.repeat(25) })])).toBe(LENGTH_MSG);
	});

	it('accepts the 3 and 24 char boundaries', () => {
		expect(validateFeaturedModels([entry({ provider_name: 'abc' })])).toBeNull();
		expect(validateFeaturedModels([entry({ provider_name: 'a'.repeat(24) })])).toBeNull();
	});

	it('returns the tag message for an 11-char tag', () => {
		expect(validateFeaturedModels([entry({ tags: ['x'.repeat(11), '', ''] })])).toBe(TAG_MSG);
	});

	it('accepts a 10-char tag', () => {
		expect(validateFeaturedModels([entry({ tags: ['x'.repeat(10), '', ''] })])).toBeNull();
	});

	it('accepts exactly 3 tags', () => {
		expect(validateFeaturedModels([entry({ tags: ['a', 'b', 'c'] })])).toBeNull();
	});

	it('returns the tag-count message for a 4th tag', () => {
		expect(
			validateFeaturedModels([
				// @ts-expect-error — exercising the runtime length check past the 3-tuple type
				entry({ tags: ['a', 'b', 'c', 'd'] })
			])
		).toBe(TAG_COUNT_MSG);
	});

	it('tag-count check runs before per-tag length — a 4th over-long tag reports the count message', () => {
		expect(
			validateFeaturedModels([
				// @ts-expect-error — exercising the runtime length check past the 3-tuple type
				entry({ tags: ['a', 'b', 'c', 'x'.repeat(99)] })
			])
		).toBe(TAG_COUNT_MSG);
	});

	it('first error wins — blank provider is reported before an over-long tag on the same entry', () => {
		expect(
			validateFeaturedModels([entry({ provider_name: '', tags: ['x'.repeat(99), '', ''] })])
		).toBe(REQUIRED_MSG);
	});

	it('reports the first bad entry when only the 2nd of several is invalid', () => {
		expect(
			validateFeaturedModels([
				entry(),
				entry({ model_id: 'b', provider_name: 'ab' }),
				entry({ model_id: 'c', provider_name: '' })
			])
		).toBe(LENGTH_MSG);
	});

	it('tolerates a missing tags array on an entry', () => {
		// @ts-expect-error — entry shape without tags, exercising the (entry.tags ?? []) guard
		expect(
			validateFeaturedModels([
				{ model_id: 'm', provider_name: 'OpenAI', featured_name: '', order: 0 }
			])
		).toBeNull();
	});
});
