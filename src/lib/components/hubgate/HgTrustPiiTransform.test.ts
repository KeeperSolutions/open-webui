// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import HgTrustPiiTransform from './HgTrustPiiTransform.svelte';
import { piiTransform } from '$lib/data/trust-centre';

describe('HgTrustPiiTransform', () => {
	it('renders the masked tokens on the output side', () => {
		render(HgTrustPiiTransform, { props: { data: piiTransform } });
		expect(screen.getByText('[PERSON_1]')).toBeInTheDocument();
		expect(screen.getByText('[PHONE_1]')).toBeInTheDocument();
		expect(screen.getByText('[ACCOUNT_1]')).toBeInTheDocument();
	});

	it('renders the caption with "Toggle masking" emphasised', () => {
		render(HgTrustPiiTransform, { props: { data: piiTransform } });
		// Emphasised word is a separate span coloured with the primary text token
		const emphasis = screen.getByText('Toggle masking');
		expect(emphasis).toBeInTheDocument();
		expect(emphasis.className).toMatch(/text-hg-text-primary/);
		// Surrounding copy is still present
		expect(screen.getByText(/Evidential control, not a pinky promise/)).toBeInTheDocument();
	});
});
