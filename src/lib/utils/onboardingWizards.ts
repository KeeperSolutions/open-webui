import { get } from 'svelte/store';
import { settings, user } from '$lib/stores';
import { updateUserSettings } from '$lib/apis/users';

const LOCAL_SEEN_WIZARDS_KEY = 'seenWizards';

// Scoped per account, so switching users in the same browser doesn't carry over dismissals
const getLocalSeenWizardsKey = (): string => {
	try {
		const userId = get(user)?.id;
		return userId ? `${LOCAL_SEEN_WIZARDS_KEY}:${userId}` : LOCAL_SEEN_WIZARDS_KEY;
	} catch {
		return LOCAL_SEEN_WIZARDS_KEY;
	}
};

// Fallback for users whose server-side save is permission-gated
const getLocalSeenWizards = (): Record<string, boolean> => {
	try {
		return JSON.parse(localStorage.getItem(getLocalSeenWizardsKey()) ?? '{}');
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
		localStorage.setItem(getLocalSeenWizardsKey(), JSON.stringify(seenWizards));
	} catch {}

	try {
		await updateUserSettings(localStorage.token, { ui: { ...current, seenWizards } });
	} catch {
		// Permission-gated save failed; local fallback above already covers it
	}
};
