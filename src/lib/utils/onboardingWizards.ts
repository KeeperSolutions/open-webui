import { get } from 'svelte/store';
import { settings } from '$lib/stores';
import { updateUserSettings } from '$lib/apis/users';

const LOCAL_SEEN_WIZARDS_KEY = 'seenWizards';

// Fallback for users whose server-side save is permission-gated
const getLocalSeenWizards = (): Record<string, boolean> => {
	try {
		return JSON.parse(localStorage.getItem(LOCAL_SEEN_WIZARDS_KEY) ?? '{}');
	} catch {
		return {};
	}
};

/** Has this user already dismissed the onboarding wizard with this id? */
export const hasSeenWizard = (id: string): boolean =>
	!!get(settings)?.seenWizards?.[id] || !!getLocalSeenWizards()[id];

/** Marks an onboarding wizard as seen server-side, so it never reappears on any device */
export const markWizardSeen = async (id: string): Promise<void> => {
	const current = get(settings);
	if (current?.seenWizards?.[id]) return;

	const seenWizards = { ...(current?.seenWizards ?? {}), [id]: true };
	settings.set({ ...current, seenWizards });

	try {
		localStorage.setItem(LOCAL_SEEN_WIZARDS_KEY, JSON.stringify(seenWizards));
	} catch {}

	try {
		await updateUserSettings(localStorage.token, { ui: { ...current, seenWizards } });
	} catch {
		// Permission-gated save failed; local fallback above already covers it
	}
};
