// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { readable } from 'svelte/store';

import FeaturedModelsModal from './FeaturedModelsModal.svelte';
import * as configsApi from '$lib/apis/configs';
import { toast } from 'svelte-sonner';

// Modal.svelte wires up focus-trap, which refuses to activate without a tabbable
// node present at mount time under jsdom. Stub it — keyboard trapping isn't under test.
vi.mock('focus-trap', () => ({
	createFocusTrap: () => ({
		activate: () => {},
		deactivate: () => {},
		pause: () => {},
		unpause: () => {}
	})
}));

// SortableJS touches DOM APIs jsdom doesn't fully implement — the editor only
// needs it for drag-reorder, which these tests don't exercise.
vi.mock('sortablejs', () => ({
	default: class {
		destroy() {}
	}
}));

// FeaturedModels.svelte reads the `models` store for the "add model" picker.
vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		models: writable([
			{ id: 'gpt-x', name: 'GPT-X' },
			{ id: 'claude', name: 'Claude' }
		])
	};
});

vi.mock('$lib/apis/configs', () => ({
	getFeaturedModels: vi.fn(),
	setFeaturedModels: vi.fn()
}));

vi.mock('svelte-sonner', () => ({
	toast: { error: vi.fn(), success: vi.fn() }
}));

const i18n = readable({
	t: (key: string, vars?: Record<string, string>) =>
		vars ? key.replace(/\{\{(\w+)\}\}/g, (_, k) => vars[k] ?? k) : key
});

const renderModal = (props: Record<string, unknown> = {}) =>
	render(FeaturedModelsModal, {
		props: { show: true, ...props },
		context: new Map([['i18n', i18n]])
	});

const validEntry = (overrides: Record<string, unknown> = {}) => ({
	model_id: 'gpt-x',
	provider_name: 'OpenAI',
	featured_name: 'Flagship',
	tags: ['fast', '', ''],
	order: 0,
	...overrides
});

const getFeaturedModels = configsApi.getFeaturedModels as unknown as ReturnType<typeof vi.fn>;
const setFeaturedModels = configsApi.setFeaturedModels as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
	vi.clearAllMocks();
	Object.defineProperty(window, 'localStorage', {
		value: { token: 'test-token', getItem: () => 'test-token', setItem: () => {} },
		writable: true
	});
});

describe('FeaturedModelsModal — load on show', () => {
	it('loads existing FEATURED_MODELS into the editor when shown', async () => {
		getFeaturedModels.mockResolvedValue({
			FEATURED_MODELS: [validEntry({ provider_name: 'OpenAI', featured_name: 'Flagship' })]
		});

		renderModal();

		await waitFor(() => expect(getFeaturedModels).toHaveBeenCalledWith('test-token'));
		// The loaded entry's provider name lands in an editor input.
		await waitFor(() => {
			const provider = document.querySelector(
				'input[id^="featured-model-provider-"]'
			) as HTMLInputElement | null;
			expect(provider?.value).toBe('OpenAI');
		});
	});

	it('shows an error toast and an empty editor when the config load fails', async () => {
		getFeaturedModels.mockRejectedValue(new Error('boom'));

		renderModal();

		await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to load configuration'));
		expect(screen.getByText('No featured models added yet.')).toBeInTheDocument();
	});

	it('normalizes a malformed stored row instead of throwing', async () => {
		// A row from before backend validation existed, or a direct DB/API
		// write it missed — no tags array, a non-string provider_name.
		// FeaturedModels.svelte binds tags by index
		// (featuredModels[idx].tags[tagIdx]); without normalization this
		// throws instead of rendering an editable (if empty) row.
		getFeaturedModels.mockResolvedValue({
			FEATURED_MODELS: [{ model_id: 'gpt-x', provider_name: null, order: 0 }]
		});

		expect(() => renderModal()).not.toThrow();

		await waitFor(() => expect(getFeaturedModels).toHaveBeenCalled());
		await waitFor(() => {
			const provider = document.querySelector(
				'input[id^="featured-model-provider-"]'
			) as HTMLInputElement | null;
			expect(provider).not.toBeNull();
			expect(provider?.value).toBe('');
		});
	});
});

describe('FeaturedModelsModal — save gate', () => {
	const clickSave = async () => {
		const saveBtn = screen.getByRole('button', { name: 'Save' });
		await fireEvent.click(saveBtn);
		return saveBtn;
	};

	it('saves the edited list via setFeaturedModels when it is valid', async () => {
		getFeaturedModels.mockResolvedValue({
			FEATURED_MODELS: [validEntry()]
		});
		setFeaturedModels.mockResolvedValue({ FEATURED_MODELS: [validEntry()] });

		renderModal();
		await waitFor(() => expect(getFeaturedModels).toHaveBeenCalled());

		await clickSave();

		await waitFor(() => expect(setFeaturedModels).toHaveBeenCalledTimes(1));
		const [token, payload] = setFeaturedModels.mock.calls[0];
		expect(token).toBe('test-token');
		expect(payload).toEqual([validEntry()]);
		expect(toast.success).toHaveBeenCalledWith('Featured models saved successfully');
	});

	it('blocks save and shows an error toast when a provider name is blank', async () => {
		getFeaturedModels.mockResolvedValue({
			FEATURED_MODELS: [validEntry({ provider_name: '' })]
		});

		renderModal();
		await waitFor(() => expect(getFeaturedModels).toHaveBeenCalled());

		await clickSave();

		expect(setFeaturedModels).not.toHaveBeenCalled();
		expect(toast.error).toHaveBeenCalledWith('Provider name is required for every featured model.');
	});

	it('blocks save when a provider name is too short', async () => {
		getFeaturedModels.mockResolvedValue({
			FEATURED_MODELS: [validEntry({ provider_name: 'ab' })]
		});

		renderModal();
		await waitFor(() => expect(getFeaturedModels).toHaveBeenCalled());

		await clickSave();

		expect(setFeaturedModels).not.toHaveBeenCalled();
		expect(toast.error).toHaveBeenCalledWith('Provider name must be between 3 and 24 characters.');
	});

	it('blocks save when a tag is too long', async () => {
		getFeaturedModels.mockResolvedValue({
			FEATURED_MODELS: [validEntry({ tags: ['x'.repeat(11), '', ''] })]
		});

		renderModal();
		await waitFor(() => expect(getFeaturedModels).toHaveBeenCalled());

		await clickSave();

		expect(setFeaturedModels).not.toHaveBeenCalled();
		expect(toast.error).toHaveBeenCalledWith('Each tag must be 10 characters or fewer.');
	});

	it('disables the Save button while the list is invalid and enables it once valid', async () => {
		getFeaturedModels.mockResolvedValue({
			FEATURED_MODELS: [validEntry({ provider_name: '' })]
		});

		renderModal();
		await waitFor(() => expect(getFeaturedModels).toHaveBeenCalled());

		const saveBtn = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
		await waitFor(() => expect(saveBtn.disabled).toBe(true));

		// Fix the provider name in the editor input → validationError clears.
		const provider = document.querySelector(
			'input[id^="featured-model-provider-"]'
		) as HTMLInputElement;
		await fireEvent.input(provider, { target: { value: 'OpenAI' } });

		await waitFor(() => expect(saveBtn.disabled).toBe(false));
	});

	it('shows the inline validation error text in the footer', async () => {
		getFeaturedModels.mockResolvedValue({
			FEATURED_MODELS: [validEntry({ provider_name: '' })]
		});

		renderModal();

		await waitFor(() =>
			expect(
				screen.getAllByText('Provider name is required for every featured model.').length
			).toBeGreaterThan(0)
		);
	});
});
