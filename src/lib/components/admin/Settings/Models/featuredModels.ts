// Featured-model field limits for the admin editor. Kept in sync with the
// backend validator in backend/open_webui/routers/configs.py
// (ModelsConfigForm._validate_featured_models).

export const PROVIDER_NAME_MIN = 3;
export const PROVIDER_NAME_MAX = 24;
export const TAG_MAX = 10;
export const MAX_TAGS = 3;

export type FeaturedModelEntry = {
	model_id: string;
	provider_name: string;
	featured_name: string;
	tags: [string, string, string];
	order: number;
};

/** A provider name is valid when, trimmed, it is 3–24 characters. */
export const isProviderNameValid = (name: string): boolean => {
	const v = (name ?? '').trim();
	return v.length >= PROVIDER_NAME_MIN && v.length <= PROVIDER_NAME_MAX;
};

/** First blocking problem across all entries, or null when the list is saveable. */
export const validateFeaturedModels = (entries: FeaturedModelEntry[]): string | null => {
	for (const entry of entries) {
		const name = (entry.provider_name ?? '').trim();
		if (!name) {
			return 'Provider name is required for every featured model.';
		}
		if (!isProviderNameValid(name)) {
			return 'Provider name must be between 3 and 24 characters.';
		}
		const tags = entry.tags ?? [];
		if (tags.length > MAX_TAGS) {
			return 'At most 3 tags are allowed per featured model.';
		}
		if (tags.some((t) => (t ?? '').length > TAG_MAX)) {
			return 'Each tag must be 10 characters or fewer.';
		}
	}
	return null;
};
