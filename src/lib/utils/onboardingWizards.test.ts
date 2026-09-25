import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/apis/users', () => ({ updateUserSettings: vi.fn().mockResolvedValue({}) }));
vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return { settings: writable({}), user: writable(undefined) };
});

import { settings, user } from '$lib/stores';
import { updateUserSettings } from '$lib/apis/users';
import { hasSeenWizard, markWizardSeen } from './onboardingWizards';

beforeEach(() => {
	vi.clearAllMocks();
	localStorage.clear();
	settings.set({});
	user.set(undefined);
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

	it('is true via the localStorage fallback even when the settings store has no record', () => {
		localStorage.setItem('seenWizards', JSON.stringify({ 'long-press-hint': true }));
		expect(hasSeenWizard('long-press-hint')).toBe(true);
	});

	it('does not leak one account\'s localStorage dismissal into another account', () => {
		user.set({ id: 'user-a' } as any);
		localStorage.setItem('seenWizards:user-a', JSON.stringify({ 'long-press-hint': true }));

		user.set({ id: 'user-b' } as any);
		expect(hasSeenWizard('long-press-hint')).toBe(false);

		user.set({ id: 'user-a' } as any);
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

	it('writes to localStorage even if updateUserSettings rejects, and does not throw', async () => {
		(updateUserSettings as any).mockRejectedValueOnce(new Error('403 Access prohibited'));

		await expect(markWizardSeen('long-press-hint')).resolves.toBeUndefined();

		expect(JSON.parse(localStorage.getItem('seenWizards') ?? '{}')).toEqual({
			'long-press-hint': true
		});
	});

	it('writes to a per-account localStorage key when a user is set', async () => {
		user.set({ id: 'user-a' } as any);

		await markWizardSeen('long-press-hint');

		expect(localStorage.getItem('seenWizards')).toBeNull();
		expect(JSON.parse(localStorage.getItem('seenWizards:user-a') ?? '{}')).toEqual({
			'long-press-hint': true
		});
	});
});
