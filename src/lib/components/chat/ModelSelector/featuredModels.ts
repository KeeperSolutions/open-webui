// Featured Models (TRAU-469) — logic extracted from Selector.svelte so it can be
// unit-tested without rendering the (Fuse + portal + virtualized) dropdown.
//
// An admin curates a short list of "featured" models via Admin → Settings → Models
// → "Featured Models". Each entry carries a display name, provider, and up to 3
// short tags. The model selector shows these first, under a "Featured" pill, using
// the curated values.

/** One entry as stored in the FEATURED_MODELS config by the admin. */
export type FeaturedModelConfig = {
	model_id: string;
	provider_name: string;
	featured_name: string;
	tags: [string, string, string];
	order: number;
};

// The `items` here are the `{ label, value, model }[]` entries Selector.svelte
// feeds to ModelItem.

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type SelectorItem = { label?: string; value: string; model?: any; [key: string]: any };

/**
 * The featured entries to actually display: those whose model exists in the
 * selector (respecting the hidden flag), sorted by their configured `order`.
 */
export const buildFeaturedModels = (
	config: FeaturedModelConfig[],
	items: SelectorItem[],
	includeHidden = false
): FeaturedModelConfig[] => {
	if (!config?.length) return [];

	const availableIds = new Set(
		items
			.filter((item) => includeHidden || !(item.model?.info?.meta?.hidden ?? false))
			.map((item) => item.value)
	);

	return [...config]
		.filter((entry) => availableIds.has(entry.model_id))
		.sort((a, b) => a.order - b.order);
};

/**
 * Map a featured entry onto the `item` shape ModelItem consumes: keep the real
 * backing model (logo, menu, connection badges) but override the display name and
 * tags with the curated values. Safe to call for an entry with no backing item —
 * used only after buildFeaturedModels() has filtered to available ids.
 */
export const featuredToItem = (entry: FeaturedModelConfig, items: SelectorItem[]) => {
	const backing = items.find((item) => item.value === entry.model_id);
	const curatedTags = (entry.tags ?? []).filter(Boolean).map((name) => ({ name }));

	return {
		...backing,
		value: entry.model_id,
		label: entry.featured_name || backing?.label,
		providerName: entry.provider_name || undefined,
		model: {
			...backing?.model,
			id: entry.model_id,
			tags: curatedTags.length > 0 ? curatedTags : (backing?.model?.tags ?? [])
		}
	};
};
