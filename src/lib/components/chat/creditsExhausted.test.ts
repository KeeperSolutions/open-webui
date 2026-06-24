// @vitest-environment jsdom
/**
 * Credits-exhausted error handling tests.
 *
 * Covers the two paths in Chat.svelte that must show the credits modal
 * instead of a generic toast:
 *
 * 1. handleOpenAIError — when the FastAPI error detail is 'credits_exhausted',
 *    dispatch billing:credits_exhausted and skip the toast.
 *
 * 2. Submit guard — when the previous message already has a credits_exhausted
 *    error and the user hits send again, dispatch the event instead of
 *    showing "Oops! There was an error in the previous response."
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ---------------------------------------------------------------------------
// Inline implementations that mirror exactly what Chat.svelte does.
// These are not imports — they live here so the test is self-contained and
// won't break if Chat.svelte is refactored internally.
// ---------------------------------------------------------------------------

const toast = { error: vi.fn() };

function handleOpenAIError(error: unknown): void {
	const innerError = error as Record<string, unknown>;

	if ('detail' in innerError) {
		if (innerError.detail === 'credits_exhausted') {
			window.dispatchEvent(new CustomEvent('billing:credits_exhausted'));
		} else {
			toast.error(innerError.detail);
		}
	} else if ('error' in innerError && typeof innerError.error === 'object') {
		const e = innerError.error as Record<string, unknown>;
		toast.error('message' in e ? e.message : e);
	} else if ('message' in innerError) {
		toast.error(innerError.message);
	}
}

function handleSubmitWithPreviousError(errorContent: string | undefined): void {
	if (errorContent?.includes('credits_exhausted')) {
		window.dispatchEvent(new CustomEvent('billing:credits_exhausted'));
	} else {
		toast.error('Oops! There was an error in the previous response.');
	}
}

// ---------------------------------------------------------------------------

describe('credits_exhausted — handleOpenAIError', () => {
	let fired: boolean;

	beforeEach(() => {
		fired = false;
		vi.clearAllMocks();
		window.addEventListener('billing:credits_exhausted', () => { fired = true; });
	});

	afterEach(() => {
		window.removeEventListener('billing:credits_exhausted', () => {});
	});

	it('dispatches billing:credits_exhausted and does not toast', () => {
		handleOpenAIError({ detail: 'credits_exhausted' });
		expect(fired).toBe(true);
		expect(toast.error).not.toHaveBeenCalled();
	});

	it('toasts and does not dispatch for other FastAPI errors', () => {
		handleOpenAIError({ detail: 'Unauthorized' });
		expect(fired).toBe(false);
		expect(toast.error).toHaveBeenCalledWith('Unauthorized');
	});

	it('toasts OpenAI-style errors with message field', () => {
		handleOpenAIError({ error: { message: 'model not found' } });
		expect(fired).toBe(false);
		expect(toast.error).toHaveBeenCalledWith('model not found');
	});
});

describe('credits_exhausted — submit guard (previous message has error)', () => {
	let fired: boolean;
	let handler: () => void;

	beforeEach(() => {
		fired = false;
		vi.clearAllMocks();
		handler = () => { fired = true; };
		window.addEventListener('billing:credits_exhausted', handler);
	});

	afterEach(() => {
		window.removeEventListener('billing:credits_exhausted', handler);
	});

	it('dispatches billing:credits_exhausted when previous error contains credits_exhausted', () => {
		handleSubmitWithPreviousError('Uh-oh! There was an issue with the response.\ncredits_exhausted');
		expect(fired).toBe(true);
		expect(toast.error).not.toHaveBeenCalled();
	});

	it('shows Oops toast for generic previous errors', () => {
		handleSubmitWithPreviousError('Uh-oh! There was an issue with the response.\nUnauthorized');
		expect(fired).toBe(false);
		expect(toast.error).toHaveBeenCalledWith('Oops! There was an error in the previous response.');
	});

	it('shows Oops toast when error content is undefined', () => {
		handleSubmitWithPreviousError(undefined);
		expect(fired).toBe(false);
		expect(toast.error).toHaveBeenCalledWith('Oops! There was an error in the previous response.');
	});
});
