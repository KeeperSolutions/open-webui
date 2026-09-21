import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/apis/users', () => ({ updateUserSettings: vi.fn().mockResolvedValue({}) }));
vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return { settings: writable({}) };
});

import { settings } from '$lib/stores';
import { updateUserSettings } from '$lib/apis/users';
import { hasSeenWizard, markWizardSeen } from './onboardingWizards';

beforeEach(() => {
	vi.clearAllMocks();
	settings.set({});
	localStorage.setItem('token', 'test-token');
});

describe('hasSeenWizard', () => {
	it('is false when the id has never been marked seen', () => {
		expect(hasSeenWizard('long-press-hint')).toBe(false);
	});

	it('is false for an unrelated wizard id even if another one was seen', () => {
		settings.set({ seenWizards: { 'other-wizard': true } });
		expect(hasSeenWizard('long-press-hint')).toBe(false);
	});

	it('is true once the id is present and true in seenWizards', () => {
		settings.set({ seenWizards: { 'long-press-hint': true } });
		expect(hasSeenWizard('long-press-hint')).toBe(true);
	});
});

describe('markWizardSeen', () => {
	it('sets the id in the settings store', async () => {
		await markWizardSeen('long-press-hint');

		let current: any;
		settings.subscribe((v) => (current = v))();
		expect(current.seenWizards).toEqual({ 'long-press-hint': true });
	});

	it('preserves other settings and other seen wizards already present', async () => {
		settings.set({ pinnedModels: ['gpt-4'], seenWizards: { 'other-wizard': true } });

		await markWizardSeen('long-press-hint');

		let current: any;
		settings.subscribe((v) => (current = v))();
		expect(current.pinnedModels).toEqual(['gpt-4']);
		expect(current.seenWizards).toEqual({ 'other-wizard': true, 'long-press-hint': true });
	});

	it('persists via updateUserSettings under the ui key', async () => {
		await markWizardSeen('long-press-hint');

		expect(updateUserSettings).toHaveBeenCalledOnce();
		const [token, payload] = (updateUserSettings as any).mock.calls[0];
		expect(token).toBe('test-token');
		expect(payload.ui.seenWizards).toEqual({ 'long-press-hint': true });
	});

	it('is a no-op (no API call) when the id is already marked seen', async () => {
		settings.set({ seenWizards: { 'long-press-hint': true } });

		await markWizardSeen('long-press-hint');

		expect(updateUserSettings).not.toHaveBeenCalled();
	});
});
