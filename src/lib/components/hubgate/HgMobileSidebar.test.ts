// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import HgMobileSidebar from './HgMobileSidebar.svelte';

// Mutable pathname the mocked `page` store reads, so each test can control the
// "current route" before rendering and assert the active-link logic for real.
let mockPathname = '/';

vi.mock('$app/stores', () => ({
	page: {
		subscribe: (fn: (value: { url: { pathname: string } }) => void) => {
			fn({ url: { pathname: mockPathname } });
			return () => {};
		}
	}
}));

describe('HgMobileSidebar', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockPathname = '/';
	});

	// ── Visibility ──────────────────────────────────────────────────────────────

	it('is hidden (translated off-screen) when open=false', () => {
		const { container } = render(HgMobileSidebar, { props: { open: false } });
		const panel = container.querySelector('[aria-hidden="true"]');
		expect(panel).not.toBeNull();
		expect(panel!.className).toContain('-translate-x-full');
	});

	it('is visible (not translated) when open=true', () => {
		const { container } = render(HgMobileSidebar, { props: { open: true } });
		const panel = container.querySelector('[aria-hidden="false"]');
		expect(panel).not.toBeNull();
		expect(panel!.className).toContain('translate-x-0');
	});

	// ── Backdrop ────────────────────────────────────────────────────────────────

	it('does not render backdrop when open=false', () => {
		render(HgMobileSidebar, { props: { open: false } });
		expect(screen.queryByRole('button', { name: 'Close menu' })).toBeNull();
	});

	it('renders backdrop button when open=true', () => {
		render(HgMobileSidebar, { props: { open: true } });
		expect(screen.getByRole('button', { name: 'Close menu' })).toBeInTheDocument();
	});

	it('clicking backdrop does not throw', async () => {
		render(HgMobileSidebar, { props: { open: true } });
		await expect(
			fireEvent.click(screen.getByRole('button', { name: 'Close menu' }))
		).resolves.not.toThrow();
	});

	// ── Nav links ───────────────────────────────────────────────────────────────

	it('contains Pricing, Privacy Policy and Terms of Use links', () => {
		render(HgMobileSidebar, { props: { open: true } });
		expect(screen.getByRole('link', { name: 'Pricing' })).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Privacy Policy' })).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Terms of Use' })).toBeInTheDocument();
	});

	it('links point to the correct hrefs', () => {
		render(HgMobileSidebar, { props: { open: true } });
		expect(screen.getByRole('link', { name: 'Pricing' })).toHaveAttribute('href', '/pricing');
		expect(screen.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute('href', '/privacy');
		expect(screen.getByRole('link', { name: 'Terms of Use' })).toHaveAttribute('href', '/terms');
	});

	it('clicking a nav link does not throw', async () => {
		render(HgMobileSidebar, { props: { open: true } });
		await expect(
			fireEvent.click(screen.getByRole('link', { name: 'Pricing' }))
		).resolves.not.toThrow();
	});

	// ── Sign In button ──────────────────────────────────────────────────────────

	it('contains a Sign In button', () => {
		render(HgMobileSidebar, { props: { open: true } });
		expect(screen.getByRole('button', { name: 'Sign In' })).toBeInTheDocument();
	});

	it('clicking Sign In does not throw', async () => {
		render(HgMobileSidebar, { props: { open: true } });
		await expect(
			fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))
		).resolves.not.toThrow();
	});

	// ── Active link ─────────────────────────────────────────────────────────────

	it('no link has aria-current="page" when pathname is "/" (no match)', () => {
		render(HgMobileSidebar, { props: { open: true } });
		screen.getAllByRole('link').forEach((link) => {
			expect(link).not.toHaveAttribute('aria-current', 'page');
		});
	});

	it('the matching link has aria-current="page" and others do not', () => {
		mockPathname = '/pricing';
		render(HgMobileSidebar, { props: { open: true } });

		expect(screen.getByRole('link', { name: 'Pricing' })).toHaveAttribute('aria-current', 'page');
		expect(screen.getByRole('link', { name: 'Privacy Policy' })).not.toHaveAttribute('aria-current', 'page');
		expect(screen.getByRole('link', { name: 'Terms of Use' })).not.toHaveAttribute('aria-current', 'page');
	});

	it('the active link is bold (font-semibold) when pathname matches', () => {
		mockPathname = '/terms';
		render(HgMobileSidebar, { props: { open: true } });

		expect(screen.getByRole('link', { name: 'Terms of Use' }).className).toContain('font-semibold');
		expect(screen.getByRole('link', { name: 'Pricing' }).className).not.toContain('font-semibold');
	});
});
