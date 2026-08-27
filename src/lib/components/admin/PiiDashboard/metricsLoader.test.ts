import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import type { MetricsResponse } from '$lib/apis/langfuse';
import { getLangfuseMetrics } from '$lib/apis/langfuse';
import { createMetricsLoader, type MetricsFetcher } from './metricsLoader';

vi.mock('$lib/apis/langfuse', () => ({ getLangfuseMetrics: vi.fn() }));

const response = (from: string, to: string, models: string[] = ['gpt-4']): MetricsResponse => ({
	from,
	to,
	rows: models.map((model) => ({ user: 'a@x.com', model, tokens: 10, cost: 0.5, observations: 1 }))
});

/** A promise whose settlement the test controls, so orderings can be forced. */
function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

describe('createMetricsLoader', () => {
	it('starts in the loading state, so no section can flash an empty one first', () => {
		const loader = createMetricsLoader(vi.fn());
		expect(get(loader)).toEqual({
			rows: [],
			windowFrom: '',
			windowTo: '',
			loading: true,
			failed: false,
			errorDetail: null
		});
	});

	it('publishes the rows and the window the backend reported', async () => {
		const fetcher: MetricsFetcher = vi.fn().mockResolvedValue(response('FROM', 'TO'));
		const loader = createMetricsLoader(fetcher);

		await loader.load('week', 7);

		const state = get(loader);
		expect(state.rows).toHaveLength(1);
		expect(state.windowFrom).toBe('FROM');
		expect(state.windowTo).toBe('TO');
		expect(state.loading).toBe(false);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
	});

	it('passes the mapped Langfuse parameters through, not the pill key', async () => {
		const fetcher = vi.fn().mockResolvedValue(response('FROM', 'TO'));
		const loader = createMetricsLoader(fetcher);

		await loader.load('day', 7);
		expect(fetcher).toHaveBeenLastCalledWith('today', undefined, expect.any(AbortSignal));

		await loader.load('custom', 14);
		expect(fetcher).toHaveBeenLastCalledWith('custom', 14, expect.any(AbortSignal));
	});

	it('aborts the in-flight request when a newer period supersedes it', async () => {
		const signals: AbortSignal[] = [];
		const first = deferred<MetricsResponse>();
		const fetcher: MetricsFetcher = vi
			.fn()
			.mockImplementation((_period, _days, signal: AbortSignal) => {
				signals.push(signal);
				return signals.length === 1 ? first.promise : Promise.resolve(response('B', 'B'));
			});
		const loader = createMetricsLoader(fetcher);

		const stale = loader.load('week', 7);
		await loader.load('month', 7);

		expect(signals[0].aborted).toBe(true);
		expect(signals[1].aborted).toBe(false);

		first.resolve(response('A', 'A'));
		await stale;
	});

	it('discards a superseded response even when it arrives last', async () => {
		const slow = deferred<MetricsResponse>();
		const fetcher: MetricsFetcher = vi
			.fn()
			.mockImplementationOnce(() => slow.promise)
			.mockImplementationOnce(() => Promise.resolve(response('FRESH', 'FRESH', ['claude'])));
		const loader = createMetricsLoader(fetcher);

		const stale = loader.load('week', 7);
		await loader.load('month', 7);

		expect(get(loader).windowFrom).toBe('FRESH');
		expect(get(loader).loading).toBe(false);

		// The superseded call now answers, with a window and rows of its own.
		slow.resolve(response('STALE', 'STALE', ['gpt-4']));
		await stale;

		const state = get(loader);
		expect(state.windowFrom).toBe('FRESH');
		expect(state.rows.map((r) => r.model)).toEqual(['claude']);
		// The newer load owns `loading`; the superseded one must not touch it.
		expect(state.loading).toBe(false);
	});

	it('does not let a superseded failure clear the fresher state', async () => {
		const slow = deferred<MetricsResponse>();
		const fetcher: MetricsFetcher = vi
			.fn()
			.mockImplementationOnce(() => slow.promise)
			.mockImplementationOnce(() => Promise.resolve(response('FRESH', 'FRESH')));
		const loader = createMetricsLoader(fetcher);

		const stale = loader.load('week', 7);
		await loader.load('month', 7);

		slow.reject({ detail: 'too late to matter' });
		await stale;

		const state = get(loader);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
		expect(state.windowFrom).toBe('FRESH');
	});

	it('reports the failure detail and clears the window it can no longer vouch for', async () => {
		const fetcher: MetricsFetcher = vi
			.fn()
			.mockResolvedValueOnce(response('FROM', 'TO'))
			.mockRejectedValueOnce({ detail: 'Langfuse credentials are not configured' });
		const loader = createMetricsLoader(fetcher);

		await loader.load('week', 7);
		expect(get(loader).windowFrom).toBe('FROM');

		await loader.load('month', 7);

		const state = get(loader);
		expect(state.failed).toBe(true);
		expect(state.errorDetail).toBe('Langfuse credentials are not configured');
		expect(state.rows).toEqual([]);
		expect(state.windowFrom).toBe('');
		expect(state.windowTo).toBe('');
		expect(state.loading).toBe(false);
	});

	it('treats an empty result as data, not as a failure', async () => {
		const fetcher: MetricsFetcher = vi.fn().mockResolvedValue({
			from: 'FROM',
			to: 'TO',
			rows: []
		});
		const loader = createMetricsLoader(fetcher);

		await loader.load('week', 7);

		const state = get(loader);
		expect(state.rows).toEqual([]);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
		expect(state.windowFrom).toBe('FROM');
	});

	it('absorbs a null response instead of publishing it as rows', async () => {
		const fetcher: MetricsFetcher = vi.fn().mockResolvedValue(null);
		const loader = createMetricsLoader(fetcher);

		await loader.load('week', 7);

		const state = get(loader);
		expect(state.rows).toEqual([]);
		expect(state.failed).toBe(true);
		// Nothing was thrown, so there is no message to show beyond the generic one.
		expect(state.errorDetail).toBeNull();
		expect(state.loading).toBe(false);
	});

	it('clears a previous failure when a retry starts', async () => {
		const fetcher: MetricsFetcher = vi
			.fn()
			.mockRejectedValueOnce({ detail: 'Upstream returned 502' })
			.mockResolvedValueOnce(response('FROM', 'TO'));
		const loader = createMetricsLoader(fetcher);

		await loader.load('week', 7);
		expect(get(loader).failed).toBe(true);

		await loader.load('week', 7);

		const state = get(loader);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
		expect(state.rows).toHaveLength(1);
	});

	it('aborts what is in flight when destroyed', async () => {
		const signals: AbortSignal[] = [];
		const pending = deferred<MetricsResponse>();
		const fetcher: MetricsFetcher = vi
			.fn()
			.mockImplementation((_period, _days, signal: AbortSignal) => {
				signals.push(signal);
				return pending.promise;
			});
		const loader = createMetricsLoader(fetcher);

		const inFlight = loader.load('week', 7);
		loader.destroy();

		expect(signals[0].aborted).toBe(true);

		pending.resolve(response('LATE', 'LATE'));
		await inFlight;

		// A destroyed loader publishes nothing further.
		expect(get(loader).windowFrom).toBe('');
		expect(get(loader).loading).toBe(true);
	});
});
/**
 * ⚠️ The propagation test below mocks the API MODULE, not the fetcher.
 *
 * `teamId` is bound inside the DEFAULT fetcher, so a test that injects its own
 * fetcher never sees it — such a test would pass green while the id was being
 * dropped on the way to the request. That is precisely the bug worth catching, so
 * the assertion has to sit on the API wrapper itself.
 *
 * Every other test in this file supplies its own fetcher and therefore never
 * reaches the mock.
 */

describe('createMetricsLoader — team id propagation', () => {
	const api = vi.mocked(getLangfuseMetrics);

	beforeEach(() => {
		api.mockReset();
		api.mockResolvedValue(response('FROM', 'TO'));
	});

	it('hands the team id to the API', async () => {
		await createMetricsLoader(undefined, 'T1').load('week', 7);
		expect(api).toHaveBeenCalledTimes(1);
		expect(api.mock.calls[0][4]).toBe('T1');
	});

	it('hands null when the screen is instance-wide', async () => {
		await createMetricsLoader().load('week', 7);
		expect(api.mock.calls[0][4]).toBeNull();
	});
});
