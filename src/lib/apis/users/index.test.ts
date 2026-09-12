// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock SvelteKit app modules used by $lib/constants
vi.mock('$app/environment', () => ({ browser: false, dev: false, building: false }));

import { updateUserSettings } from './index';

// Covers a real bug: a network-level failure (fetch() itself rejecting —
// offline, DNS, connection refused) used to resolve to `null` instead of
// throwing, because the .catch() handler read `err.detail`, which is
// undefined on a bare TypeError (only a parsed HTTP-error body has `.detail`).
// `if (error) throw error` then silently skipped the throw. Callers that rely
// on this function throwing on ANY failure — e.g. Selector.svelte's
// setDefaultHandler, which must not report "Default model updated" or update
// its local store when the save never reached the server — reported success
// on a plain network outage.
describe('updateUserSettings', () => {
	const ORIGINAL_FETCH = globalThis.fetch;

	beforeEach(() => {
		vi.restoreAllMocks();
	});

	afterEach(() => {
		// @ts-expect-error - test cleanup
		globalThis.fetch = ORIGINAL_FETCH;
	});

	it('resolves with the parsed body on a successful save', async () => {
		const body = { ui: { theme: 'dark' } };
		// @ts-expect-error - test stub
		globalThis.fetch = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => body
		});

		await expect(updateUserSettings('tok', { ui: { theme: 'dark' } })).resolves.toEqual(body);
	});

	it('throws the server-provided detail on a non-ok HTTP response', async () => {
		// @ts-expect-error - test stub
		globalThis.fetch = vi.fn().mockResolvedValue({
			ok: false,
			json: async () => ({ detail: 'Invalid settings payload' })
		});

		await expect(updateUserSettings('tok', {})).rejects.toBe('Invalid settings payload');
	});

	it('throws on a network-level failure (fetch itself rejects) instead of resolving to null', async () => {
		// @ts-expect-error - test stub
		globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));

		await expect(updateUserSettings('tok', {})).rejects.toBeTruthy();
	});

	it('network-level failure error carries a useful message, not undefined', async () => {
		// @ts-expect-error - test stub
		globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));

		await expect(updateUserSettings('tok', {})).rejects.toBe('Failed to fetch');
	});
});
