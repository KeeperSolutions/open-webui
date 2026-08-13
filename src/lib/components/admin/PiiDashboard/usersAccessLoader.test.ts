import { describe, it, expect, vi } from 'vitest';
import { get } from 'svelte/store';
import type { AccessUser } from './sections/usersAccess';
import {
	createUsersAccessLoader as createLoaderWithFetchers,
	USERS_MAX,
	USERS_PAGE_SIZE,
	type GroupsFetcher,
	type UsersFetcher,
	type UsersPage
} from './usersAccessLoader';

/** Likewise the group list — the tests that are about it pass their own. */
const noGroups: GroupsFetcher = async () => [];

/**
 * Defaults the group fetcher so the existing cases stay about what they were
 * about. The group list has its own describe block below, where it is explicit.
 */
const createUsersAccessLoader = (users: UsersFetcher, groups: GroupsFetcher = noGroups) =>
	createLoaderWithFetchers(users, groups);

const mkUser = (n: number): AccessUser => ({
	id: `id-${n}`,
	name: `User ${n}`,
	email: `u${n}@x.com`,
	role: 'user',
	group_ids: []
});

/** A directory of `total` users, served in pages of `USERS_PAGE_SIZE`. */
const pagedDirectory =
	(total: number, onPage?: (page: number) => void): UsersFetcher =>
	async (page) => {
		onPage?.(page);
		const start = (page - 1) * USERS_PAGE_SIZE;
		return {
			users: Array.from({ length: Math.max(0, Math.min(USERS_PAGE_SIZE, total - start)) }, (_, i) =>
				mkUser(start + i + 1)
			),
			total
		};
	};

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

describe('createUsersAccessLoader', () => {
	it('starts in the loading state with an empty directory', () => {
		const loader = createUsersAccessLoader(vi.fn());
		expect(get(loader)).toEqual({
			users: [],
			truncatedUsers: null,
			policyGroups: [],
			loading: true,
			failed: false,
			errorDetail: null
		});
	});

	it('fetches a single page exactly once', async () => {
		const pages: number[] = [];
		const loader = createUsersAccessLoader(pagedDirectory(12, (p) => pages.push(p)));

		await loader.load();

		expect(pages).toEqual([1]);
		const state = get(loader);
		expect(state.users).toHaveLength(12);
		expect(state.truncatedUsers).toBeNull();
		expect(state.loading).toBe(false);
		expect(state.failed).toBe(false);
	});

	it('walks every page and joins them in order', async () => {
		const pages: number[] = [];
		const loader = createUsersAccessLoader(pagedDirectory(75, (p) => pages.push(p)));

		await loader.load();

		expect(pages).toEqual([1, 2, 3]);
		const users = get(loader).users;
		expect(users).toHaveLength(75);
		expect(users[0].id).toBe('id-1');
		expect(users[74].id).toBe('id-75');
	});

	it('stops without truncating when the directory lands exactly on the ceiling', async () => {
		const pages: number[] = [];
		const loader = createUsersAccessLoader(pagedDirectory(USERS_MAX, (p) => pages.push(p)));

		await loader.load();

		const state = get(loader);
		expect(state.users).toHaveLength(USERS_MAX);
		expect(state.truncatedUsers).toBeNull();
		// 200 users at 30 per page = 7 pages, and not one more.
		expect(pages).toEqual([1, 2, 3, 4, 5, 6, 7]);
	});

	it('cuts inside the page that crosses the ceiling and says so', async () => {
		const pages: number[] = [];
		const loader = createUsersAccessLoader(pagedDirectory(812, (p) => pages.push(p)));

		await loader.load();

		const state = get(loader);
		// Cut by user, not rounded up to the page boundary.
		expect(state.users).toHaveLength(USERS_MAX);
		expect(state.users[USERS_MAX - 1].id).toBe(`id-${USERS_MAX}`);
		expect(state.truncatedUsers).toEqual({ shown: USERS_MAX, total: 812 });
		// The page after the boundary is never requested.
		expect(pages).toEqual([1, 2, 3, 4, 5, 6, 7]);
		expect(state.failed).toBe(false);
	});

	it('truncates when a page boundary lands exactly on the ceiling', async () => {
		// Deliberately not USERS_PAGE_SIZE: 30 never sums to exactly 200, so with the
		// real page size the boundary is never touched and the cut-off condition goes
		// untested. A page size that divides the ceiling exercises it.
		const PAGE = 50;
		const pages: number[] = [];
		const fetcher: UsersFetcher = async (page) => {
			pages.push(page);
			const start = (page - 1) * PAGE;
			return {
				users: Array.from({ length: PAGE }, (_, i) => mkUser(start + i + 1)),
				total: 500
			};
		};
		const loader = createUsersAccessLoader(fetcher);

		await loader.load();

		const state = get(loader);
		expect(state.users).toHaveLength(USERS_MAX);
		expect(state.truncatedUsers).toEqual({ shown: USERS_MAX, total: 500 });
		// Stops on the page that reaches the ceiling; never asks for the next.
		expect(pages).toEqual([1, 2, 3, 4]);
	});

	it('reports a failure on the first page', async () => {
		const fetcher: UsersFetcher = vi.fn().mockRejectedValue({ detail: 'Unauthorized' });
		const loader = createUsersAccessLoader(fetcher);

		await loader.load();

		const state = get(loader);
		expect(state.failed).toBe(true);
		expect(state.errorDetail).toBe('Unauthorized');
		expect(state.users).toEqual([]);
		expect(state.loading).toBe(false);
	});

	it('discards the pages it already had when a middle page fails', async () => {
		const fetcher: UsersFetcher = vi.fn().mockImplementation(async (page: number) => {
			if (page === 3) throw { detail: 'Upstream returned 502' };
			return { users: Array.from({ length: USERS_PAGE_SIZE }, (_, i) => mkUser(i)), total: 150 };
		});
		const loader = createUsersAccessLoader(fetcher);

		await loader.load();

		const state = get(loader);
		expect(state.failed).toBe(true);
		expect(state.errorDetail).toBe('Upstream returned 502');
		// Never a partial directory: 2 of 5 pages would assert something untrue.
		expect(state.users).toEqual([]);
		expect(state.truncatedUsers).toBeNull();
	});

	it('drops a directory it can no longer vouch for when a reload fails', async () => {
		const fetcher: UsersFetcher = vi
			.fn()
			.mockResolvedValueOnce({ users: [mkUser(1), mkUser(2)], total: 2 })
			.mockRejectedValueOnce({ detail: 'Unauthorized' });
		const loader = createUsersAccessLoader(fetcher);

		await loader.load();
		expect(get(loader).users).toHaveLength(2);

		await loader.load();

		const state = get(loader);
		expect(state.failed).toBe(true);
		// Stale rows must not survive a failure — the table would present them as
		// current, and the truncation notice would describe a fetch that never ran.
		expect(state.users).toEqual([]);
		expect(state.truncatedUsers).toBeNull();
	});

	it('stops the sequence when destroyed mid-way, without sending the next page', async () => {
		const pages: number[] = [];
		const gate = deferred<UsersPage>();
		const fetcher: UsersFetcher = vi.fn().mockImplementation((page: number) => {
			pages.push(page);
			if (page === 3) return gate.promise;
			return Promise.resolve({
				users: Array.from({ length: USERS_PAGE_SIZE }, (_, i) => mkUser(i)),
				total: 300
			});
		});
		const loader = createUsersAccessLoader(fetcher);

		const inFlight = loader.load();
		// Wait until the sequence is actually parked on page 3, rather than
		// counting microtasks — the number of ticks per page is an implementation
		// detail and changed once already.
		while (pages.length < 3) await new Promise((r) => setTimeout(r, 0));
		loader.destroy();
		gate.resolve({ users: [mkUser(99)], total: 300 });
		await inFlight;

		expect(pages).toEqual([1, 2, 3]);
		// Page 4 was never requested, and nothing was published.
		expect(pages).not.toContain(4);
		const state = get(loader);
		expect(state.users).toEqual([]);
		expect(state.loading).toBe(true);
	});

	it('lets a newer load supersede a sequence already in progress', async () => {
		const gate = deferred<UsersPage>();
		let call = 0;
		const fetcher: UsersFetcher = vi.fn().mockImplementation((page: number) => {
			call++;
			// The first sequence parks on its first page.
			if (call === 1) return gate.promise;
			return Promise.resolve({ users: [mkUser(page + 500)], total: 1 });
		});
		const loader = createUsersAccessLoader(fetcher);

		const stale = loader.load();
		await loader.load();

		expect(get(loader).users).toEqual([mkUser(501)]);

		gate.resolve({ users: [mkUser(1), mkUser(2)], total: 2 });
		await stale;

		// The superseded sequence publishes nothing, not even its loading flag.
		const state = get(loader);
		expect(state.users).toEqual([mkUser(501)]);
		expect(state.loading).toBe(false);
	});

	it('treats an empty directory as data, not as a failure', async () => {
		const loader = createUsersAccessLoader(pagedDirectory(0));

		await loader.load();

		const state = get(loader);
		expect(state.users).toEqual([]);
		expect(state.truncatedUsers).toBeNull();
		expect(state.failed).toBe(false);
		expect(state.loading).toBe(false);
	});

	it('stops when a page comes back empty despite a larger reported total', async () => {
		const pages: number[] = [];
		const fetcher: UsersFetcher = vi.fn().mockImplementation(async (page: number) => {
			pages.push(page);
			return page === 1
				? { users: [mkUser(1)], total: 999 }
				: { users: [] as AccessUser[], total: 999 };
		});
		const loader = createUsersAccessLoader(fetcher);

		await loader.load();

		// Trusts what arrived over what was promised, instead of looping forever.
		expect(pages).toEqual([1, 2]);
		expect(get(loader).users).toHaveLength(1);
		expect(get(loader).failed).toBe(false);
	});

	it('absorbs a null page instead of publishing it as users', async () => {
		const fetcher: UsersFetcher = vi.fn().mockResolvedValue(null);
		const loader = createUsersAccessLoader(fetcher);

		await loader.load();

		const state = get(loader);
		expect(state.users).toEqual([]);
		expect(Array.isArray(state.users)).toBe(true);
		expect(state.failed).toBe(true);
		expect(state.errorDetail).toBeNull();
		expect(state.loading).toBe(false);
	});

	it('clears a previous failure when a retry starts', async () => {
		const fetcher: UsersFetcher = vi
			.fn()
			.mockRejectedValueOnce({ detail: 'Unauthorized' })
			.mockResolvedValueOnce({ users: [mkUser(1)], total: 1 });
		const loader = createUsersAccessLoader(fetcher);

		await loader.load();
		expect(get(loader).failed).toBe(true);

		await loader.load();

		const state = get(loader);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
		expect(state.users).toHaveLength(1);
	});

	it('does not report a failure that lands after it was destroyed', async () => {
		const gate = deferred<UsersPage>();
		const fetcher: UsersFetcher = vi.fn().mockImplementation(() => gate.promise);
		const loader = createUsersAccessLoader(fetcher);

		const inFlight = loader.load();
		loader.destroy();
		gate.reject({ detail: 'too late to matter' });
		await inFlight;

		const state = get(loader);
		expect(state.failed).toBe(false);
		expect(state.errorDetail).toBeNull();
	});
});

describe('createUsersAccessLoader — policy groups', () => {
	const oneUser: UsersFetcher = async () => ({ users: [mkUser(1)], total: 1 });

	it('publishes only the groups that carry the policy', async () => {
		const groups: GroupsFetcher = async () => [
			{ id: 'g1', name: 'Policy', permissions: { chat: { pii_masking_enforced: true } } },
			{ id: 'g2', name: 'Everyone', permissions: { chat: { pii_masking_enforced: false } } },
			{ id: 'g3', name: 'No permissions', permissions: null }
		];
		const loader = createUsersAccessLoader(oneUser, groups);

		await loader.load();

		expect(get(loader).policyGroups).toEqual([{ id: 'g1', name: 'Policy' }]);
	});

	it('fails the section when the group list cannot be read', async () => {
		// Not optional data: without it an enforced row cannot tell one source
		// from several, and the action would offer a removal that changes nothing.
		const loader = createUsersAccessLoader(oneUser, async () => null);

		await loader.load();

		const state = get(loader);
		expect(state.failed).toBe(true);
		expect(state.users).toEqual([]);
		expect(state.policyGroups).toEqual([]);
		// Refused deliberately, not by falling over: without the guard the null
		// reaches `policyGroupsOf`, and the section still fails — but with a raw
		// TypeError shown to the admin instead of the same quiet "could not load"
		// the other two fetchers give. Same outcome, different thing on screen.
		expect(state.errorDetail).toBeNull();
	});

	it('publishes nothing when the run was superseded before the group list', async () => {
		const gate = deferred<UsersPage>();
		let call = 0;
		const users: UsersFetcher = () => {
			call++;
			return call === 1 ? gate.promise : Promise.resolve({ users: [mkUser(9)], total: 1 });
		};
		const groupCalls: number[] = [];
		const groups: GroupsFetcher = async () => {
			groupCalls.push(1);
			return [{ id: 'g1', name: 'Policy', permissions: { chat: { pii_masking_enforced: true } } }];
		};
		const loader = createUsersAccessLoader(users, groups);

		const stale = loader.load();
		await loader.load();
		gate.resolve({ users: [mkUser(1)], total: 1 });
		await stale;

		expect(groupCalls).toHaveLength(1);
		expect(get(loader).users).toEqual([mkUser(9)]);
	});
});
