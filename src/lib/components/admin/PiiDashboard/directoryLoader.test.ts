import { describe, it, expect, vi } from 'vitest';
import { get } from 'svelte/store';
import { createDirectoryLoader, type DirectoryFetcher } from './directoryLoader';

const USERS = [
	{ id: 'id-1', email: 'a@x.com', name: 'Ana' },
	{ id: 'id-2', email: 'b@x.com', name: 'Bruno' }
];

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

describe('createDirectoryLoader', () => {
	it('starts in the loading state with an empty directory', () => {
		const loader = createDirectoryLoader(vi.fn());
		expect(get(loader)).toEqual({
			users: [],
			loading: true,
			failed: false,
			errorDetail: null
		});
	});

	it('publishes the users it fetched', async () => {
		const fetcher: DirectoryFetcher = vi.fn().mockResolvedValue({ users: USERS, total: 2 });
		const loader = createDirectoryLoader(fetcher);

		await loader.load();

		const state = get(loader);
		expect(state.users).toEqual(USERS);
		expect(state.loading).toBe(false);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
	});

	it('treats an empty directory as data, not as a failure', async () => {
		const fetcher: DirectoryFetcher = vi.fn().mockResolvedValue({ users: [], total: 0 });
		const loader = createDirectoryLoader(fetcher);

		await loader.load();

		const state = get(loader);
		expect(state.users).toEqual([]);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
	});

	it('reports the failure detail', async () => {
		const fetcher: DirectoryFetcher = vi.fn().mockRejectedValue({ detail: 'Unauthorized' });
		const loader = createDirectoryLoader(fetcher);

		await loader.load();

		const state = get(loader);
		expect(state.failed).toBe(true);
		expect(state.errorDetail).toBe('Unauthorized');
		expect(state.users).toEqual([]);
		expect(state.loading).toBe(false);
	});

	it('clears a previous failure when a retry starts', async () => {
		const fetcher: DirectoryFetcher = vi
			.fn()
			.mockRejectedValueOnce({ detail: 'Unauthorized' })
			.mockResolvedValueOnce({ users: USERS, total: 2 });
		const loader = createDirectoryLoader(fetcher);

		await loader.load();
		expect(get(loader).failed).toBe(true);

		await loader.load();

		const state = get(loader);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
		expect(state.users).toEqual(USERS);
	});

	it('aborts what is in flight when destroyed', async () => {
		const signals: AbortSignal[] = [];
		const pending = deferred<{ users: typeof USERS }>();
		const fetcher: DirectoryFetcher = vi.fn().mockImplementation((signal: AbortSignal) => {
			signals.push(signal);
			return pending.promise;
		});
		const loader = createDirectoryLoader(fetcher);

		const inFlight = loader.load();
		loader.destroy();

		expect(signals[0].aborted).toBe(true);

		pending.resolve({ users: USERS });
		await inFlight;

		// A destroyed loader publishes nothing further.
		const state = get(loader);
		expect(state.users).toEqual([]);
		expect(state.loading).toBe(true);
	});

	it('does not let a superseded load overwrite the one that replaced it', async () => {
		// The retry race: the first attempt is still in flight when the admin presses
		// Retry, and it rejects AFTER the second one has already succeeded. Without
		// supersede handling the late rejection blanks the table and reports an error
		// that no longer applies.
		const first = deferred<{ users: typeof USERS }>();
		const second = deferred<{ users: typeof USERS }>();
		const calls = [first, second];
		let index = 0;
		const fetcher: DirectoryFetcher = vi.fn().mockImplementation(() => calls[index++].promise);
		const loader = createDirectoryLoader(fetcher);

		const stale = loader.load();
		const fresh = loader.load();

		second.resolve({ users: USERS });
		await fresh;

		first.reject({ detail: 'the attempt that was superseded' });
		await stale;

		const state = get(loader);
		expect(state.users).toEqual(USERS);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
		expect(state.loading).toBe(false);
	});

	it('does not report a failure that lands after it was destroyed', async () => {
		const pending = deferred<{ users: typeof USERS }>();
		const fetcher: DirectoryFetcher = vi.fn().mockImplementation(() => pending.promise);
		const loader = createDirectoryLoader(fetcher);

		const inFlight = loader.load();
		loader.destroy();

		pending.reject({ detail: 'too late to matter' });
		await inFlight;

		const state = get(loader);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
	});

	it('absorbs a null response instead of publishing it as users', async () => {
		const fetcher: DirectoryFetcher = vi.fn().mockResolvedValue(null);
		const loader = createDirectoryLoader(fetcher);

		await loader.load();

		const state = get(loader);
		expect(state.users).toEqual([]);
		// The client resolves to null on a network-layer failure that carries no
		// detail; the section treated that as an empty directory, not an error.
		expect(state.failed).toBe(false);
		expect(state.loading).toBe(false);
	});

	it('absorbs a response with no users key', async () => {
		const fetcher: DirectoryFetcher = vi.fn().mockResolvedValue({});
		const loader = createDirectoryLoader(fetcher);

		await loader.load();

		expect(get(loader).users).toEqual([]);
		expect(get(loader).failed).toBe(false);
	});
});
