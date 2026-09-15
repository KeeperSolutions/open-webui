import { describe, it, expect } from 'vitest';
import { isDefaultModel, toggleDefaultModel } from './defaultModel';

describe('isDefaultModel()', () => {
	it('is true only when the model is the sole entry in settings.models', () => {
		expect(isDefaultModel({ models: ['gpt-x'] }, 'gpt-x')).toBe(true);
	});

	it('is false when a different model is the default', () => {
		expect(isDefaultModel({ models: ['gpt-x'] }, 'claude-y')).toBe(false);
	});

	it('is false when there is no default', () => {
		expect(isDefaultModel({ models: [] }, 'gpt-x')).toBe(false);
		expect(isDefaultModel({}, 'gpt-x')).toBe(false);
		expect(isDefaultModel(null, 'gpt-x')).toBe(false);
		expect(isDefaultModel(undefined, 'gpt-x')).toBe(false);
	});

	it('is false when multiple models are selected (compare mode), even if one matches', () => {
		expect(isDefaultModel({ models: ['gpt-x', 'claude-y'] }, 'gpt-x')).toBe(false);
	});
});

describe('toggleDefaultModel()', () => {
	it('returns null for an empty model id (nothing to do)', () => {
		expect(toggleDefaultModel({ models: ['gpt-x'] }, '')).toBeNull();
	});

	it('sets a new default, replacing any previous one', () => {
		const result = toggleDefaultModel({ models: ['gpt-x'] }, 'claude-y');
		expect(result).toEqual({ nextSettings: { models: ['claude-y'] }, cleared: false });
	});

	it('sets a default when there was none', () => {
		expect(toggleDefaultModel({ models: [] }, 'gpt-x')).toEqual({
			nextSettings: { models: ['gpt-x'] },
			cleared: false
		});
		expect(toggleDefaultModel({}, 'gpt-x')).toEqual({
			nextSettings: { models: ['gpt-x'] },
			cleared: false
		});
	});

	it('clears the default when toggling the model that is already default', () => {
		expect(toggleDefaultModel({ models: ['gpt-x'] }, 'gpt-x')).toEqual({
			nextSettings: { models: [] },
			cleared: true
		});
	});

	it('preserves the rest of the settings object', () => {
		const settings = { models: ['gpt-x'], theme: 'dark', system: 'be brief' };
		const result = toggleDefaultModel(settings, 'claude-y');
		expect(result?.nextSettings).toEqual({
			models: ['claude-y'],
			theme: 'dark',
			system: 'be brief'
		});
	});

	it('does not mutate the input settings object', () => {
		const settings = { models: ['gpt-x'] };
		toggleDefaultModel(settings, 'claude-y');
		expect(settings.models).toEqual(['gpt-x']);
	});

	it('tolerates a null/undefined settings object', () => {
		expect(toggleDefaultModel(null, 'gpt-x')).toEqual({
			nextSettings: { models: ['gpt-x'] },
			cleared: false
		});
		expect(toggleDefaultModel(undefined, 'gpt-x')).toEqual({
			nextSettings: { models: ['gpt-x'] },
			cleared: false
		});
	});

	it('setting a different model while one is default is not treated as a clear (the bug)', () => {
		// Regression: picking model B while A is default must produce models:['B'],
		// not clear the default. `cleared` must be false so the toast/UI is right.
		const result = toggleDefaultModel({ models: ['model-a'] }, 'model-b');
		expect(result).toEqual({ nextSettings: { models: ['model-b'] }, cleared: false });
	});
});
