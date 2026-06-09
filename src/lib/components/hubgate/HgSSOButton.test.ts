// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

import HgSSOButton from './HgSSOButton.svelte';

beforeEach(() => vi.clearAllMocks());

// ─── navigation ───────────────────────────────────────────────────────────────

describe('click navigation', () => {
	it('navigates to href when provided', async () => {
		Object.defineProperty(window, 'location', {
			value: { href: '' },
			writable: true
		});

		render(HgSSOButton, { props: { provider: 'google', href: 'https://example.com/oauth/google' } });
		await fireEvent.click(screen.getByRole('button'));

		expect(window.location.href).toBe('https://example.com/oauth/google');
	});

	it('does not navigate when href is empty', async () => {
		Object.defineProperty(window, 'location', { value: { href: '' }, writable: true });

		render(HgSSOButton, { props: { provider: 'google', href: '' } });
		await fireEvent.click(screen.getByRole('button'));

		expect(window.location.href).toBe('');
	});

	it('does not navigate when href is omitted (default)', async () => {
		Object.defineProperty(window, 'location', { value: { href: '' }, writable: true });

		render(HgSSOButton, { props: { provider: 'google' } });
		await fireEvent.click(screen.getByRole('button'));

		expect(window.location.href).toBe('');
	});
});

// ─── provider labels ──────────────────────────────────────────────────────────

describe('provider labels', () => {
	it('shows "Continue with Google" for google provider', () => {
		render(HgSSOButton, { props: { provider: 'google' } });
		expect(screen.getByText('Continue with Google')).toBeInTheDocument();
	});

	it('shows "Continue with Microsoft" for microsoft provider', () => {
		render(HgSSOButton, { props: { provider: 'microsoft' } });
		expect(screen.getByText('Continue with Microsoft')).toBeInTheDocument();
	});

	it('navigates to the correct microsoft href on click', async () => {
		Object.defineProperty(window, 'location', { value: { href: '' }, writable: true });

		render(HgSSOButton, {
			props: { provider: 'microsoft', href: 'https://example.com/oauth/microsoft' }
		});
		await fireEvent.click(screen.getByRole('button'));

		expect(window.location.href).toBe('https://example.com/oauth/microsoft');
	});
});
