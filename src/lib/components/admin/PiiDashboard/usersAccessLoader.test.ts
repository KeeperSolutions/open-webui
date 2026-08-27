import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { getGroups } from '$lib/apis/groups';
import { getUsers } from '$lib/apis/users';
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
			enforceTargets: [],
			// Enforcing groups excluded for belonging to a team — the empty state
			// needs it to tell its two causes apart.
			teamOnlyPolicyGroups: 0,
			// Enforcing groups excluded for granting more than masking — the same
			// question as above, asked of a different exclusion.
			broadPolicyGroups: 0,
			// The addressed team's own policy group; `null` until a scoped load lands.
			teamGroupId: null,
			mayManagePolicy: false,
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

		expect(get(loader).policyGroups).toEqual([{ id: 'g1', name: 'Policy', isTeamGroup: false }]);
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
/**
 * ⚠️ The propagation tests below mock the API MODULE, not the fetcher.
 *
 * `teamId` is bound inside the DEFAULT fetcher, so a test that injects its own
 * fetcher never sees it — such a test would pass green while the id was being
 * dropped on the way to the request. That is precisely the bug worth catching, so
 * the assertion has to sit on the API wrapper itself.
 *
 * Every other test in this file supplies its own fetchers and never reaches these.
 */

vi.mock('$lib/apis/users', () => ({ getUsers: vi.fn() }));
vi.mock('$lib/apis/groups', () => ({ getGroups: vi.fn() }));

describe('createUsersAccessLoader — team id propagation', () => {
	const usersApi = vi.mocked(getUsers);
	const groupsApi = vi.mocked(getGroups);

	beforeEach(() => {
		usersApi.mockReset();
		groupsApi.mockReset();
		usersApi.mockResolvedValue({ users: [], total: 0 });
		groupsApi.mockResolvedValue([]);
	});

	it('hands the team id to the users API', async () => {
		await createLoaderWithFetchers(undefined, undefined, 'T1').load();
		expect(usersApi).toHaveBeenCalledTimes(1);
		expect(usersApi.mock.calls[0][6]).toBe('T1');
	});

	it('hands null to the users API when the screen is instance-wide', async () => {
		await createLoaderWithFetchers().load();
		expect(usersApi.mock.calls[0][6]).toBeNull();
	});

	it('does NOT hand the team id to the groups API', async () => {
		// `GET /groups/` is not scoped by team in level A, and passing an id there
		// would advertise a scoping that does not exist.
		await createLoaderWithFetchers(undefined, undefined, 'T1').load();
		expect(groupsApi).toHaveBeenCalledTimes(1);
		expect(groupsApi.mock.calls[0].slice(1)).not.toContain('T1');
	});
});

describe('createUsersAccessLoader — naming and destinations are separate lists', () => {
	const oneUser: UsersFetcher = async () => ({ users: [mkUser(1)], total: 1 });

	it('publishes a team group for naming but not as a destination', async () => {
		/**
		 * ⚠️ One list served both questions, and that is what put a raw UUID in
		 * the removal dialog: the team group was filtered out of the only list
		 * `rowActionFor` could name from, so it fell back to the id.
		 */
		const groups: GroupsFetcher = async () => [
			{ id: 'g1', name: 'Policy', permissions: { chat: { pii_masking_enforced: true } } },
			{
				id: 'g-team',
				name: 'PII — Acme · abcdef01',
				permissions: { chat: { pii_masking_enforced: true } },
				is_team_group: true
			}
		];
		const loader = createUsersAccessLoader(oneUser, groups);

		await loader.load();

		const state = get(loader);
		expect(state.policyGroups.map((g) => g.id)).toEqual(['g1', 'g-team']);
		expect(state.enforceTargets.map((g) => g.id)).toEqual(['g1']);
	});
});

// `import.meta.url` is not a file: URL under vite, so the path is resolved from
// the project root, which is where vitest runs.
const dashboardSource = readFileSync(
	resolve(process.cwd(), 'src/lib/components/admin/PiiDashboard/PiiDashboard.svelte'),
	'utf-8'
);

/**
 * The `<UsersAccess … />` element as written. Scoped rather than searched over
 * the whole file so an assertion about a prop cannot be met by the same text on
 * one of the neighbouring sections.
 */
const usersAccessCallSite = (() => {
	const start = dashboardSource.indexOf('<UsersAccess');
	if (start < 0) throw new Error('PiiDashboard.svelte no longer mounts <UsersAccess>');
	const end = dashboardSource.indexOf('/>', start);
	if (end < 0) throw new Error('<UsersAccess> is no longer self-closing; this slice is wrong');
	return dashboardSource.slice(start, end + 2);
})();

describe('the dashboard hands each list to the prop of the same name', () => {
	/**
	 * ⚠️ Written because a mutation SURVIVED: swapping `enforceTargets` for
	 * `policyGroups` in `PiiDashboard.svelte` broke nothing.
	 *
	 * Every other test here passes the two lists to `rowActionFor` or to
	 * `UsersAccess` directly, so all of them keep passing while the one line that
	 * connects the loader to the component quietly puts team groups back in the
	 * Enforce dropdown — the defect this split was made to close.
	 *
	 * Structural rather than behavioural because the alternative is mounting the
	 * whole dashboard, with three loaders and four stores, to prove one binding.
	 * Only this pair is pinned: other props are deliberately renamed on the way
	 * through (`truncated` reads `truncatedUsers`), so a blanket rule would be
	 * false.
	 */
	it.each(['policyGroups', 'enforceTargets', 'broadPolicyGroups'])(
		'passes %s from the field of that name',
		(prop) => {
			expect(dashboardSource).toContain(`${prop}={$usersAccess.${prop}}`);
		}
	);

	it('never feeds the naming list to the destination prop', () => {
		expect(dashboardSource).not.toContain('enforceTargets={$usersAccess.policyGroups}');
	});
});

describe('the dashboard hands each permission to the prop of that name', () => {
	/**
	 * ⚠️ The same shape as the mutation above, with a worse outcome. Swapping
	 * `mayAct` and `mayManagePolicy` at this one line survives the whole suite:
	 * `usersAccess.test.ts` calls `rowActionFor` with a viewer it builds itself,
	 * and `UsersAccess.component.test.ts` sets both props by hand. A team owner
	 * would be handed the `Manage` link — the way to the admin screen — while the
	 * two membership buttons disappear.
	 *
	 * The `viewer` object introduced in G-C5 closed the swap INSIDE the function.
	 * This closes the swap while PASSING. They are two different places, and
	 * neither test covers the other.
	 *
	 * Three props by name, never a rule over all of them: `truncated` reads
	 * `truncatedUsers` deliberately, so "prop X takes the field of that name" is
	 * false for the component as a whole. An allow-list is the only form that
	 * does not fail on correct code — see the last case here.
	 */
	it.each([
		// Derived from the ROLE, not from the loader — see `mayActFor`. The
		// shorthand `{mayAct}` is also the tail of `mayAct={mayAct}`, so either
		// spelling satisfies this.
		['mayAct', '{mayAct}'],
		['mayManagePolicy', 'mayManagePolicy={$usersAccess.mayManagePolicy}'],
		['teamGroupId', 'teamGroupId={$usersAccess.teamGroupId}']
	])('binds %s at the call site', (_prop, binding) => {
		expect(usersAccessCallSite).toContain(binding);
	});

	it('never lets the reported permission decide who may act', () => {
		// The load-bearing half. Under a genuine swap the shorthand `{mayAct}` is
		// still present — as the tail of `mayManagePolicy={mayAct}` — so the
		// positive case above is satisfied BY the mutation. Only this sees it.
		expect(usersAccessCallSite).not.toMatch(/mayAct=\{\$usersAccess\./);
	});

	it('never lets the role stand in for the reported permission', () => {
		expect(usersAccessCallSite).not.toContain('mayManagePolicy={mayAct}');
	});

	it('never passes the addressed team where its policy group belongs', () => {
		// `teamId` is a team id and `teamGroupId` a group id. The owner's criterion
		// is membership of the latter, so this swap hides both buttons without
		// erroring — and reads as "the owner has no policy" rather than as a bug.
		expect(usersAccessCallSite).not.toContain('teamGroupId={teamId}');
	});

	it('does not claim that prop and field always share a name', () => {
		// The counter-example that forces the allow-list, pinned so the reason
		// given above cannot quietly stop being true.
		expect(usersAccessCallSite).toContain('truncated={$usersAccess.truncatedUsers}');
	});
});

describe('createUsersAccessLoader — the permission the server reports', () => {
	/**
	 * ⚠️ Written because a mutation SURVIVED: reading the field as `!== false`
	 * broke nothing, since every payload on file carried it.
	 *
	 * A payload that OMITS it is the case that matters, and it is reachable — an
	 * older backend, or any response built before this field existed. The two
	 * readings differ only there, and they differ in the dangerous direction:
	 * absent would become permitted, and a viewer with no right to act would be
	 * shown buttons the server then refuses.
	 *
	 * Same failure as M5 in G-B7, where the corpus lacked the explicit `false`
	 * the backend always sends.
	 */
	const page =
		(over: Record<string, unknown>): UsersFetcher =>
		async () => ({
			users: [mkUser(1)],
			total: 1,
			...over
		});

	it('reports the permission when the server grants it', async () => {
		const loader = createUsersAccessLoader(page({ may_manage_team_policy: true }));
		await loader.load();
		expect(get(loader).mayManagePolicy).toBe(true);
	});

	it('reports no permission when the server denies it', async () => {
		const loader = createUsersAccessLoader(page({ may_manage_team_policy: false }));
		await loader.load();
		expect(get(loader).mayManagePolicy).toBe(false);
	});

	it('⚠️ reports no permission when the field is absent', async () => {
		// Absent is not "yes". A permission has to be granted to exist.
		const loader = createUsersAccessLoader(page({}));
		await loader.load();
		expect(get(loader).mayManagePolicy).toBe(false);
	});

	it('⚠️ reports no permission for any value that is not literally true', async () => {
		// `'true'`, `1` and `null` are all things a payload can carry and none of
		// them is a grant.
		for (const value of ['true', 1, null, undefined]) {
			const loader = createUsersAccessLoader(page({ may_manage_team_policy: value }));
			await loader.load();
			expect(get(loader).mayManagePolicy).toBe(false);
		}
	});
});
