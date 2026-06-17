// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import { readable } from 'svelte/store';

import MaskedValuesList from './MaskedValuesList.svelte';

// i18n mock: return the key and interpolate vars (repo component-test convention).
const i18n = readable({
	t: (key: string, vars?: Record<string, string>) => {
		if (!vars) return key;
		return key.replace(/\{\{(\w+)\}\}/g, (_, k) => vars[k] ?? k);
	}
});

const renderList = (props: Record<string, unknown>) =>
	render(MaskedValuesList, { props, context: new Map([['i18n', i18n]]) });

const mk = (type: string, value: string) => ({ key: JSON.stringify([type, value]), type, value });

const SEARCH = 'Search masked values';

describe('MaskedValuesList', () => {
	it('renders each value alongside its entity type', () => {
		renderList({ items: [mk('PERSON', 'Ivan Horvat'), mk('EMAIL', 'a@b.com')] });
		expect(screen.getByText('Ivan Horvat')).toBeTruthy();
		expect(screen.getByText('a@b.com')).toBeTruthy();
		expect(screen.getByText('PERSON')).toBeTruthy();
		expect(screen.getByText('EMAIL')).toBeTruthy();
	});

	it('always shows the local-reconstruction disclaimer', () => {
		renderList({ items: [mk('PERSON', 'Ivan')] });
		expect(
			screen.getByText('Values are reconstructed locally — they never leave your browser.')
		).toBeTruthy();
	});

	it('humanizes underscored entity codes (US_SSN -> US SSN)', () => {
		renderList({ items: [mk('US_SSN', '123-45-6789')] });
		expect(screen.getByText('US SSN')).toBeTruthy();
	});

	it('stays a flat list (no search) at or below the group threshold', () => {
		const items = Array.from({ length: 8 }, (_, i) => mk('PERSON', `value-${i}`));
		renderList({ items });
		expect(screen.queryByPlaceholderText(SEARCH)).toBeNull();
	});

	it('switches to a searchable view above the threshold', () => {
		const items = Array.from({ length: 9 }, (_, i) => mk('PERSON', `value-${i}`));
		renderList({ items });
		expect(screen.getByPlaceholderText(SEARCH)).toBeTruthy();
	});

	it('respects a custom groupThreshold', () => {
		const items = Array.from({ length: 3 }, (_, i) => mk('PERSON', `value-${i}`));
		renderList({ items, groupThreshold: 2 });
		expect(screen.getByPlaceholderText(SEARCH)).toBeTruthy();
	});

	it('filters the list by the search query', async () => {
		const items = [
			...Array.from({ length: 8 }, (_, i) => mk('PERSON', `person-${i}`)),
			mk('EMAIL', 'needle@example.com')
		];
		renderList({ items });
		await fireEvent.input(screen.getByPlaceholderText(SEARCH), {
			target: { value: 'needle' }
		});
		expect(screen.getByText('needle@example.com')).toBeTruthy();
		expect(screen.queryByText('person-0')).toBeNull();
	});

	it('matches the search against the entity type too', async () => {
		const items = [
			...Array.from({ length: 8 }, (_, i) => mk('PERSON', `person-${i}`)),
			mk('EMAIL', 'someone@example.com')
		];
		renderList({ items });
		await fireEvent.input(screen.getByPlaceholderText(SEARCH), {
			target: { value: 'email' }
		});
		expect(screen.getByText('someone@example.com')).toBeTruthy();
		expect(screen.queryByText('person-0')).toBeNull();
	});

	it('shows an empty state when nothing matches', async () => {
		const items = Array.from({ length: 9 }, (_, i) => mk('PERSON', `person-${i}`));
		renderList({ items });
		await fireEvent.input(screen.getByPlaceholderText(SEARCH), {
			target: { value: 'zzz-no-match' }
		});
		expect(screen.getByText('No results found')).toBeTruthy();
	});
});
