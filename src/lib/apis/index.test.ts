// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$app/environment', () => ({ browser: false, dev: false, building: false }));

import { generateAutoCompletion } from './index';

// The backend reads the chat's masking toggle from `features.pii_masking`.
// Without it, autocomplete falls back to the stored preference and ignores the toggle.
describe('generateAutoCompletion', () => {
	const ORIGINAL_FETCH = globalThis.fetch;

	afterEach(() => {
		globalThis.fetch = ORIGINAL_FETCH;
	});

	function stubFetch() {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ choices: [{ message: { content: '{"text": "done"}' } }] })
		});
		// @ts-expect-error - test stub
		globalThis.fetch = fetchMock;
		return fetchMock;
	}

	function sentBody(fetchMock: ReturnType<typeof vi.fn>) {
		return JSON.parse(fetchMock.mock.calls[0][1].body);
	}

	it.each([true, false])('sends features.pii_masking = %s', async (flag) => {
		const fetchMock = stubFetch();
		await generateAutoCompletion('t', 'gpt-4', 'Hi', undefined, 'search query', undefined, flag);
		expect(sentBody(fetchMock).features).toEqual({ pii_masking: flag });
	});

	it('omits features when no flag is given', async () => {
		const fetchMock = stubFetch();
		await generateAutoCompletion('t', 'gpt-4', 'Hi');
		expect(sentBody(fetchMock)).not.toHaveProperty('features');
	});
});
