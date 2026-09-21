import { get } from 'svelte/store';
import { settings } from '$lib/stores';
import { updateUserSettings } from '$lib/apis/users';

/** Has this user already dismissed the onboarding wizard with this id? */
export const hasSeenWizard = (id: string): boolean => !!get(settings)?.seenWizards?.[id];

/** Marks an onboarding wizard as seen server-side, so it never reappears on any device */
export const markWizardSeen = async (id: string): Promise<void> => {
	const current = get(settings);
	if (current?.seenWizards?.[id]) return;

	const seenWizards = { ...(current?.seenWizards ?? {}), [id]: true };
	settings.set({ ...current, seenWizards });
	await updateUserSettings(localStorage.token, { ui: { ...current, seenWizards } });
};
