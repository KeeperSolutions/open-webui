// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import HgTrustSectionHeader from './HgTrustSectionHeader.svelte';

const props = { eyebrow: 'the promise', title: 'Six commitments your security team can verify.', description: 'Every claim maps to a control.' };

describe('HgTrustSectionHeader', () => {
	it('renders the eyebrow, title and description', () => {
		render(HgTrustSectionHeader, { props });
		expect(screen.getByText('the promise')).toBeInTheDocument();
		expect(screen.getByRole('heading', { level: 2, name: props.title })).toBeInTheDocument();
		expect(screen.getByText(props.description)).toBeInTheDocument();
	});

	it('applies uppercase accent style to the eyebrow', () => {
		render(HgTrustSectionHeader, { props });
		expect(screen.getByText('the promise').className).toMatch(/uppercase/);
	});
});
