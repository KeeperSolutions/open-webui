// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';

import HgInputBar from './HgInputBar.svelte';

// NOTE: HgInputBar uses Svelte 5 createEventDispatcher to emit 'open'.
// In Svelte 5, dispatcher events are only materialised when a parent component
// listens via on:open — without a parent, the event is silently dropped.
// These tests verify the keyboard guard logic by spying on the native click
// handler and asserting which keys are handled vs ignored.

beforeEach(() => vi.clearAllMocks());

const renderBar = () => render(HgInputBar);

// ─── keyboard accessibility ───────────────────────────────────────────────────

describe('keyboard handling', () => {
	it('Enter key calls preventDefault (treated as activation)', async () => {
		renderBar();
		const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
		const preventDefaultSpy = vi.spyOn(event, 'preventDefault');

		screen.getByRole('button').dispatchEvent(event);

		expect(preventDefaultSpy).toHaveBeenCalled();
	});

	it('Space key calls preventDefault (treated as activation)', async () => {
		renderBar();
		const event = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
		const preventDefaultSpy = vi.spyOn(event, 'preventDefault');

		screen.getByRole('button').dispatchEvent(event);

		expect(preventDefaultSpy).toHaveBeenCalled();
	});

	it('Tab key does not call preventDefault', async () => {
		renderBar();
		const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
		const preventDefaultSpy = vi.spyOn(event, 'preventDefault');

		screen.getByRole('button').dispatchEvent(event);

		expect(preventDefaultSpy).not.toHaveBeenCalled();
	});

	it('Escape key does not call preventDefault', async () => {
		renderBar();
		const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
		const preventDefaultSpy = vi.spyOn(event, 'preventDefault');

		screen.getByRole('button').dispatchEvent(event);

		expect(preventDefaultSpy).not.toHaveBeenCalled();
	});
});

// ─── accessibility attributes ─────────────────────────────────────────────────

describe('a11y', () => {
	it('root element has role=button', () => {
		renderBar();
		expect(screen.getByRole('button')).toBeInTheDocument();
	});

	it('root element is keyboard focusable (tabindex=0)', () => {
		renderBar();
		expect(screen.getByRole('button')).toHaveAttribute('tabindex', '0');
	});
});

// ─── decorative chrome (no nested interactive controls) ───────────────────────

// HgInputBar is a fake teaser input: the whole bar is the only interactive
// element (opens the auth modal). The PII toggle and action icons are purely
// decorative — there must be no nested button/switch inside the role=button
// wrapper (avoids the nested-interactive a11y anti-pattern).

describe('decorative chrome', () => {
	it('exposes exactly one interactive element (the bar itself)', () => {
		renderBar();
		expect(screen.getAllByRole('button')).toHaveLength(1);
	});

	it('has no nested switch control', () => {
		renderBar();
		expect(screen.queryByRole('switch')).toBeNull();
	});

	it('renders the PII Masking label and shield (decorative)', () => {
		renderBar();
		expect(screen.getByText('PII Masking')).toBeInTheDocument();
	});
});
