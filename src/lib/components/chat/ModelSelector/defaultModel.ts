// Per-model "Set as default" logic (TRAU-542) — extracted from Selector.svelte so
// it can be unit-tested without rendering the dropdown.
//
// The user's default model is stored as `settings.models` — a single-element
// array holding the model id, or empty when there is no user default. Setting a
// default replaces the array; unsetting clears it (the next new chat then falls
// back to the admin `DEFAULT_MODELS`, or the first available model).

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Settings = { models?: string[]; [key: string]: any };

/** True when `modelId` is the user's current sole default. */
export const isDefaultModel = (settings: Settings | null | undefined, modelId: string): boolean => {
	const models = settings?.models ?? [];
	return models.length === 1 && models[0] === modelId;
};

export type SetDefaultResult = {
	/** The settings object to persist (store + DB). */
	nextSettings: Settings;
	/** Whether this toggled the default OFF (i.e. `modelId` was already default). */
	cleared: boolean;
};

/**
 * Compute the settings update for toggling `modelId` as the default. Returns
 * `null` when there is nothing to do (no model id). Toggling a model that is
 * already the default clears the default entirely.
 */
export const toggleDefaultModel = (
	settings: Settings | null | undefined,
	modelId: string
): SetDefaultResult | null => {
	if (!modelId) return null;
	const cleared = isDefaultModel(settings, modelId);
	return {
		nextSettings: { ...(settings ?? {}), models: cleared ? [] : [modelId] },
		cleared
	};
};
