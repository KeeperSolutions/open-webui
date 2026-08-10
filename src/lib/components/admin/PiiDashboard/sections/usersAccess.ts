import type { MetricRow } from '$lib/apis/langfuse';
import { getPiiMaskingDefault } from '$lib/utils/pii';
import { grantedModelIds, type ModelRecord } from '../modelAccess';
import { normalizeUserKey } from './costAnalytics';

export type UserStatus = 'pending' | 'inactive' | 'active';

/** The subset of an OWUI directory user this section renders. */
export type AccessUser = {
	id: string;
	name: string;
	email: string;
	role: string;
	group_ids?: string[];
	settings?: { ui?: Record<string, unknown> } | null;
};

export type UserRow = {
	id: string;
	name: string;
	email: string;
	role: string;
	status: UserStatus;
	maskingEnabled: boolean;
	cost: number;
	grantedCount: number;
	allModels: boolean;
};

/**
 * The registered catalogue this section measures access against.
 *
 * `truncated` matters: "granted equals total" cannot be claimed when the total
 * is unknown, so a cut-off catalogue suppresses `allModels` rather than
 * asserting it from a partial count.
 */
export type ModelCatalogue = { models: ModelRecord[]; truncated: boolean };

/**
 * Status of one directory user.
 *
 * A strict hierarchy, not a chain of conditions: `pending` outranks everything
 * because such an account is locked out at `get_verified_user` and can never
 * spend — so its zero is structural, and reads as a different admin action
 * (approve access) than an idle licence (reallocate).
 */
export function statusOf(user: AccessUser, hasUsage: boolean): UserStatus {
	if (user.role === 'pending') return 'pending';
	return hasUsage ? 'active' : 'inactive';
}

/**
 * Cost per Langfuse identity, keyed by `normalizeUserKey`.
 *
 * Keys are Langfuse's, not the directory's: a key with no matching account
 * still appears here. Attribution to accounts happens in `buildRows`.
 */
export function costByUser(rows: MetricRow[]): Map<string, number> {
	const out = new Map<string, number>();
	for (const r of rows) {
		const key = normalizeUserKey(r.user);
		out.set(key, (out.get(key) ?? 0) + r.cost);
	}
	return out;
}

/**
 * Which directory user, if any, owns a given Langfuse identity key.
 *
 * A user is traceable under both their email and their id, so both claim the
 * key. When two users would claim the same key — one's email equal to another's
 * id — the first in directory order wins, so a row is never counted twice.
 */
function claimKeys(users: AccessUser[]): Map<string, number> {
	const owner = new Map<string, number>();
	users.forEach((u, index) => {
		for (const traced of [u.email, u.id]) {
			const key = normalizeUserKey(traced ?? '');
			if (key && !owner.has(key)) owner.set(key, index);
		}
	});
	return owner;
}

/**
 * One row per directory user, with the spend attributed to them in this window.
 *
 * Rows whose identity matches no account are deliberately dropped here and
 * accounted for by `unattributedCost`, so the two together are exhaustive.
 */
export function buildRows(
	users: AccessUser[],
	metricRows: MetricRow[],
	catalogue: ModelCatalogue = { models: [], truncated: false }
): UserRow[] {
	const owner = claimKeys(users);
	const cost = new Array<number>(users.length).fill(0);
	// Usage is "was seen at all", not "cost is non-zero": a refund can net a
	// user's spend to exactly zero without meaning they were idle.
	const seen = new Array<boolean>(users.length).fill(false);

	for (const r of metricRows) {
		const index = owner.get(normalizeUserKey(r.user));
		if (index === undefined) continue;
		cost[index] += r.cost;
		seen[index] = true;
	}

	const total = catalogue.models.length;

	return users.map((u, index) => {
		const grantedCount = grantedModelIds(u, catalogue.models).size;
		return {
			id: u.id,
			name: u.name,
			email: u.email,
			role: u.role,
			status: statusOf(u, seen[index]),
			maskingEnabled: getPiiMaskingDefault(u.settings?.ui ?? {}),
			cost: cost[index],
			grantedCount,
			allModels: !catalogue.truncated && total > 0 && grantedCount === total
		};
	});
}

/**
 * Which i18n key renders a granted-model count.
 *
 * Returns the key, not the translated string, so `$i18n.t()` stays in the
 * component — and so the choice between singular and plural is reachable from a
 * unit test, which the markup around it is not.
 *
 * Two keys rather than i18next plurals: the `en-US` catalogue carries empty
 * values, so `_one`/`_other` resolve back to the base key and a single grant
 * reads "1 models". This applies to every counted string in the app, not just
 * this one.
 *
 * Zero takes the plural, which English agrees with. The cell renders an em dash
 * at zero instead of calling this, so that branch is unreachable from the table
 * — the function stays total anyway rather than leaving a hole for the next
 * caller to find.
 */
export function modelsCountKey(count: number): string {
	return count === 1 ? '1 model' : '{{count}} models';
}

/**
 * Spend Langfuse recorded against an identity no account here claims.
 *
 * Exhaustive with `buildRows`: every row lands in exactly one of the two, so
 * the table column plus this figure reconcile with section 3's total cost.
 */
export function unattributedCost(metricRows: MetricRow[], users: AccessUser[]): number {
	const owner = claimKeys(users);
	let total = 0;
	for (const r of metricRows) {
		if (owner.has(normalizeUserKey(r.user))) continue;
		total += r.cost;
	}
	return total;
}
