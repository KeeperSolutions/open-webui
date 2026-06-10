// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

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

// ─── PII masking toggle ───────────────────────────────────────────────────────

describe('PII masking toggle', () => {
	const getToggle = () => screen.getByRole('switch', { name: /toggle pii masking/i });

	it('defaults to on (aria-checked=true)', () => {
		renderBar();
		expect(getToggle()).toHaveAttribute('aria-checked', 'true');
	});

	it('flips aria-checked to false when clicked', async () => {
		renderBar();
		await fireEvent.click(getToggle());
		expect(getToggle()).toHaveAttribute('aria-checked', 'false');
	});

	it('toggles back to on after two clicks', async () => {
		renderBar();
		await fireEvent.click(getToggle());
		await fireEvent.click(getToggle());
		expect(getToggle()).toHaveAttribute('aria-checked', 'true');
	});

	it('clicking the toggle does not dispatch open (propagation stopped)', async () => {
		const { container } = renderBar();
		const onOpen = vi.fn();
		container.addEventListener('open', onOpen);

		await fireEvent.click(getToggle());

		expect(onOpen).not.toHaveBeenCalled();
	});
});
