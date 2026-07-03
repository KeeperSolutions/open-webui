// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import HgBadgeFixture from './HgBadge.fixture.svelte';

describe('HgBadge', () => {
	it('renders the label from the slot', () => {
		render(HgBadgeFixture, { props: { variant: 'info', label: 'EU inference' } });
		expect(screen.getByText('EU inference')).toBeInTheDocument();
	});

	it('applies the success variant classes', () => {
		render(HgBadgeFixture, { props: { variant: 'success', label: 'Certified' } });
		expect(screen.getByText('Certified').className).toMatch(/bg-hg-success-50/);
	});

	it('applies the info variant classes', () => {
		render(HgBadgeFixture, { props: { variant: 'info', label: 'EU inference' } });
		expect(screen.getByText('EU inference').className).toMatch(/bg-hg-info-bg/);
	});

	it('defaults to the neutral variant', () => {
		render(HgBadgeFixture, { props: { label: 'Role' } });
		expect(screen.getByText('Role').className).toMatch(/bg-hg-bg-muted/);
	});
});
