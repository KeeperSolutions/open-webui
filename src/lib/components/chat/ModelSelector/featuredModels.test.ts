import { describe, it, expect } from 'vitest';
import { buildFeaturedModels, featuredToItem, type FeaturedModelConfig } from './featuredModels';

// A model tag as it reaches the frontend: the backend's `normalize_tags`
// (models/models.py) coerces every tag to `{ name: string }`.
type ModelTag = { name: string };

// Minimal SelectorItem factory — mirrors the { label, value, model }[] shape
// Selector.svelte feeds to ModelItem. `model.tags` holds ModelTag objects;
// `model.info.meta.hidden` is the flag buildFeaturedModels honours.
const item = (
	value: string,
	label: string,
	{ hidden = false, tags = [] as string[] }: { hidden?: boolean; tags?: string[] } = {}
) => ({
	value,
	label,
	model: {
		id: value,
		tags: tags.map((name): ModelTag => ({ name })),
		info: { meta: { hidden } }
	}
});

// A FEATURED_MODELS config entry as stored by the admin. `tags` is three plain
// strings (curated tag text), not ModelTag objects — featuredToItem maps them.
const featured = (
	model_id: string,
	overrides: Partial<FeaturedModelConfig> = {}
): FeaturedModelConfig => ({
	model_id,
	provider_name: '',
	featured_name: '',
	tags: ['', '', ''],
	order: 0,
	...overrides
});

describe('buildFeaturedModels()', () => {
	it('returns [] for an empty config', () => {
		expect(buildFeaturedModels([], [item('a', 'A')])).toEqual([]);
	});

	it('returns [] when config is null/undefined', () => {
		// @ts-expect-error — exercising the runtime guard
		expect(buildFeaturedModels(null, [item('a', 'A')])).toEqual([]);
	});

	it('sorts entries by their configured order', () => {
		const config = [
			featured('c', { order: 2 }),
			featured('a', { order: 0 }),
			featured('b', { order: 1 })
		];
		const items = [item('a', 'A'), item('b', 'B'), item('c', 'C')];
		expect(buildFeaturedModels(config, items).map((e) => e.model_id)).toEqual(['a', 'b', 'c']);
	});

	it('drops entries whose model is not in the selector items', () => {
		const config = [featured('a', { order: 0 }), featured('ghost', { order: 1 })];
		const items = [item('a', 'A')];
		expect(buildFeaturedModels(config, items).map((e) => e.model_id)).toEqual(['a']);
	});

	it('drops entries backed by a hidden model when includeHidden is false', () => {
		const config = [featured('a'), featured('secret')];
		const items = [item('a', 'A'), item('secret', 'Secret', { hidden: true })];
		expect(buildFeaturedModels(config, items).map((e) => e.model_id)).toEqual(['a']);
	});

	it('keeps hidden-backed entries when includeHidden is true', () => {
		const config = [featured('a'), featured('secret')];
		const items = [item('a', 'A'), item('secret', 'Secret', { hidden: true })];
		expect(buildFeaturedModels(config, items, true).map((e) => e.model_id)).toEqual(['a', 'secret']);
	});
});

describe('featuredToItem()', () => {
	const items = [item('gpt-x', 'GPT-X', { tags: ['backing-tag'] })];

	it('overrides the label with featured_name when set', () => {
		const result = featuredToItem(featured('gpt-x', { featured_name: 'Flagship' }), items);
		expect(result.label).toBe('Flagship');
	});

	it('falls back to the backing label when featured_name is empty', () => {
		const result = featuredToItem(featured('gpt-x', { featured_name: '' }), items);
		expect(result.label).toBe('GPT-X');
	});

	it('uses curated tags, dropping empty strings, as { name } objects', () => {
		const result = featuredToItem(featured('gpt-x', { tags: ['fast', '', ''] }), items);
		expect(result.model.tags).toEqual([{ name: 'fast' }]);
	});

	it('falls back to backing model tags when no curated tags are set', () => {
		const result = featuredToItem(featured('gpt-x', { tags: ['', '', ''] }), items);
		expect(result.model.tags).toEqual([{ name: 'backing-tag' }]);
	});

	it('carries provider_name through as providerName, undefined when blank', () => {
		expect(featuredToItem(featured('gpt-x', { provider_name: 'OpenAI' }), items).providerName).toBe(
			'OpenAI'
		);
		expect(featuredToItem(featured('gpt-x', { provider_name: '' }), items).providerName).toBe(
			undefined
		);
	});

	it('does not throw when there is no backing item', () => {
		const result = featuredToItem(featured('missing', { featured_name: 'Orphan' }), items);
		expect(result.value).toBe('missing');
		expect(result.label).toBe('Orphan');
		expect(result.model.id).toBe('missing');
		expect(result.model.tags).toEqual([]);
	});
});
