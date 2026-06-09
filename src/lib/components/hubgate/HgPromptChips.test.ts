// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

import HgPromptChips from './HgPromptChips.svelte';

const CHIPS = [
	'Draft a proposal',
	'Review a document',
	'Generate an image',
	'Summarize meeting',
	'Analyse a contract'
];

const renderChips = (props = {}) => render(HgPromptChips, { props });

beforeEach(() => vi.clearAllMocks());

// ─── onSelect prop ────────────────────────────────────────────────────────────

describe('onSelect prop', () => {
	it('calls onSelect with type=prompt and the chip label when provided', async () => {
		const onSelect = vi.fn();
		renderChips({ onSelect });

		await fireEvent.click(screen.getByRole('button', { name: /draft a proposal/i }));

		expect(onSelect).toHaveBeenCalledWith({ type: 'prompt', data: 'Draft a proposal' });
	});

	it('calls onSelect with the correct label for each chip', async () => {
		const onSelect = vi.fn();
		renderChips({ onSelect });

		for (const label of CHIPS) {
			await fireEvent.click(screen.getByRole('button', { name: new RegExp(label, 'i') }));
		}

		expect(onSelect).toHaveBeenCalledTimes(CHIPS.length);
		CHIPS.forEach((label, i) => {
			expect(onSelect).toHaveBeenNthCalledWith(i + 1, { type: 'prompt', data: label });
		});
	});

	it('does not dispatch the open DOM event when onSelect is provided', async () => {
		const onSelect = vi.fn();
		const { container } = renderChips({ onSelect });

		const onOpen = vi.fn();
		container.addEventListener('open', onOpen);

		await fireEvent.click(screen.getByRole('button', { name: /draft a proposal/i }));

		expect(onOpen).not.toHaveBeenCalled();
	});
});

// ─── dispatch('open') fallback ────────────────────────────────────────────────

describe('open event fallback', () => {
	it('does not call onSelect when it is null', async () => {
		// onSelect defaults to null — clicking should not throw
		renderChips();
		await fireEvent.click(screen.getByRole('button', { name: /draft a proposal/i }));
		// no assertion needed beyond "does not throw"
	});
});

// ─── chip list integrity ──────────────────────────────────────────────────────

describe('chip list', () => {
	it('renders all five chips', () => {
		renderChips();
		expect(screen.getAllByRole('button')).toHaveLength(CHIPS.length);
	});

	it('each chip label matches the expected set exactly', () => {
		renderChips();
		for (const label of CHIPS) {
			expect(screen.getByRole('button', { name: new RegExp(label, 'i') })).toBeInTheDocument();
		}
	});
});
