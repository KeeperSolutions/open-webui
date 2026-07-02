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

	it('renders the caption', () => {
		render(HgTrustPiiTransform, { props: { data: piiTransform } });
		expect(screen.getByText(/Toggle masking to see what leaves your perimeter/)).toBeInTheDocument();
	});
});
