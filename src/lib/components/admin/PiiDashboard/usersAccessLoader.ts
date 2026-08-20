import { writable, type Readable } from 'svelte/store';
import { getUsers } from '$lib/apis/users';
import { getGroups } from '$lib/apis/groups';
import { describeLoadError } from './sections/costAnalytics';
import {
	policyGroupsOf,
	type AccessUser,
	type GroupRecord,
	type PolicyGroup
} from './sections/usersAccess';

/**
 * Hard ceiling on the directory this section will render.
 *
 * The table has no pagination, and the fetch is sequential and shares the
 * dashboard's first paint with the metrics call — so the ceiling is set where
 * a worse round-trip still stays tolerable (200 users ≈ 7 requests) rather
 * than at the largest directory imaginable. Raising it later against a
 * measured RTT is cheap; lowering it after a complaint is not.
 */
export const USERS_MAX = 200;

/** Mirrors `PAGE_ITEM_COUNT` in backend/open_webui/routers/users.py. */
export const USERS_PAGE_SIZE = 30;

/** How much of a list is on screen, when it is not all of it. */
export type Truncation = { shown: number; total: number };

export type UsersAccessState = {
	users: AccessUser[];
	/**
	 * The groups that carry the policy, for the row action's destination.
	 *
	 * Derived from the group list on every load, never remembered: a destination
	 * held in component state or localStorage would be per-admin, so the same
	 * action would land in different groups with nothing recording why.
	 */
	policyGroups: PolicyGroup[];
	truncatedUsers: Truncation | null;
	loading: boolean;
	failed: boolean;
	errorDetail: string | null;
};

export type UsersPage = { users: AccessUser[]; total: number };

/**
 * One page each. Injectable so pagination, truncation and abort can be driven
 * from tests without a network.
 */
export type UsersFetcher = (page: number, signal: AbortSignal) => Promise<UsersPage | null>;
/** Not paginated: `GET /groups/` returns the lot, and groups are few. */
export type GroupsFetcher = (signal: AbortSignal) => Promise<GroupRecord[] | null>;

export type UsersAccessLoader = Readable<UsersAccessState> & {
	load: () => Promise<void>;
	destroy: () => void;
};

const INITIAL: UsersAccessState = {
	users: [],
	policyGroups: [],
	truncatedUsers: null,
	loading: true,
	failed: false,
	errorDetail: null
};

const defaultGroupsFetcher: GroupsFetcher = () => getGroups(localStorage.token);

/** Sentinel for "the sequence was superseded or destroyed; publish nothing". */
const ABORTED = Symbol('aborted');

type Collected<T> = { items: T[]; truncated: Truncation | null };

/**
 * ⚠️ `teamId` last, default users fetcher built here - see `metricsLoader.ts`.
 *
 * The GROUPS fetcher deliberately does not take it: `GET /groups/` already returns
 * only the groups the caller belongs to, and level A has no team-to-group bridge to
 * scope it by. Passing a team id there would suggest a scoping that does not exist.
 */
export function createUsersAccessLoader(
	usersFetcher?: UsersFetcher,
	groupsFetcher: GroupsFetcher = defaultGroupsFetcher,
	teamId: string | null = null
): UsersAccessLoader {
	const fetchUsers: UsersFetcher =
		usersFetcher ??
		((page, signal) =>
			getUsers(localStorage.token, undefined, undefined, undefined, page, signal, teamId));

	const { subscribe, update } = writable<UsersAccessState>({ ...INITIAL });

	let inFlight: AbortController | null = null;

	const fail = (errorDetail: string | null) =>
		update((s) => ({
			...s,
			failed: true,
			errorDetail,
			// A list this section cannot vouch for in full is not shown in part: a
			// compliance table silently listing 3 of 7 pages asserts something untrue.
			users: [],
			policyGroups: [],
			truncatedUsers: null
		}));

	/**
	 * Walks a paginated endpoint to exhaustion or to `max`, whichever comes first.
	 * Returns ABORTED when the run was superseded, so the caller publishes nothing.
	 */
	const collect = async <T>(
		fetchPage: (page: number, signal: AbortSignal) => Promise<{ items: T[]; total: number } | null>,
		max: number,
		controller: AbortController
	): Promise<Collected<T> | typeof ABORTED | null> => {
		const items: T[] = [];

		for (let page = 1; ; page++) {
			const res = await fetchPage(page, controller.signal);
			// The only interleaving point in the sequence: nothing but synchronous
			// code runs between this check and the next request, so a guard here
			// stops the whole run, not just the page that was in flight.
			if (controller.signal.aborted) return ABORTED;
			if (!res) return null;

			items.push(...res.items);

			if (items.length >= max) {
				// Cut inside the page that crossed the line, so what is published is
				// exactly the ceiling and never rounded up to a page boundary.
				items.length = max;
				// A list of exactly the ceiling is complete, not truncated — the flag
				// means "there is more you are not seeing".
				return { items, truncated: res.total > max ? { shown: max, total: res.total } : null };
			}

			if (items.length >= res.total) break;
			// A page that returns nothing while `total` still promises more would
			// otherwise loop forever; trust what arrived, not what was promised.
			if (res.items.length === 0) break;
		}

		return { items, truncated: null };
	};

	const load = async () => {
		inFlight?.abort();
		const controller = new AbortController();
		inFlight = controller;

		update((s) => ({ ...s, loading: true, failed: false, errorDetail: null }));

		try {
			const usersResult = await collect<AccessUser>(
				async (page, signal) => {
					const res = await fetchUsers(page, signal);
					return res ? { items: res.users, total: res.total } : null;
				},
				USERS_MAX,
				controller
			);
			if (usersResult === ABORTED) return;
			if (!usersResult) {
				fail(null);
				return;
			}

			// The row action needs to know which groups carry the policy. Treated
			// as load-bearing rather than optional: without it every enforced row
			// would look like it has a single source, and `Remove` would appear on
			// rows where removal changes nothing, which the row action must never offer.
			const groups = await groupsFetcher(controller.signal);
			if (controller.signal.aborted) return;
			if (!groups) {
				fail(null);
				return;
			}

			update((s) => ({
				...s,
				users: usersResult.items,
				policyGroups: policyGroupsOf(groups),
				truncatedUsers: usersResult.truncated,
				failed: false,
				errorDetail: null
			}));
		} catch (e: unknown) {
			if (controller.signal.aborted) return;
			fail(describeLoadError(e));
		} finally {
			if (!controller.signal.aborted) update((s) => ({ ...s, loading: false }));
		}
	};

	const destroy = () => {
		inFlight?.abort();
		inFlight = null;
	};

	return { subscribe, load, destroy };
}
