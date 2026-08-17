import type { MetricRow } from '$lib/apis/langfuse';

export type Agg = { name: string; tokens: number; cost: number; detail?: string };
export type BarDatum = Agg & { percent: number };

/** The subset of an OWUI user this module needs to label a Langfuse row. */
/** `role` is optional because only `inactiveUsers` reads it; see its note. */
export type DirectoryUser = { id: string; email: string; name?: string; role?: string };

/**
 * Identity key for a Langfuse `user` value. Langfuse and OWUI can disagree on
 * casing/whitespace, so every comparison of the two goes through this.
 */
export function normalizeUserKey(user: string): string {
	return (user ?? '').trim().toLowerCase();
}

/**
 * Groups by `key`, but labels each group with `label` of its FIRST row, so a
 * normalised grouping key never leaks into what the user sees.
 */
function aggregateBy(
	rows: MetricRow[],
	key: (r: MetricRow) => string,
	label: (r: MetricRow) => string = key,
	detail: (r: MetricRow) => string | undefined = () => undefined
): Agg[] {
	const map = new Map<string, Agg>();
	for (const r of rows) {
		const k = key(r);
		const existing = map.get(k);
		if (existing) {
			existing.tokens += r.tokens;
			existing.cost += r.cost;
		} else {
			map.set(k, { name: label(r), tokens: r.tokens, cost: r.cost, detail: detail(r) });
		}
	}
	return [...map.values()].sort((a, b) => b.cost - a.cost);
}

export function aggregateByModel(rows: MetricRow[]): Agg[] {
	return aggregateBy(rows, (r) => r.model);
}

/**
 * Display name for a Langfuse `user` value, keyed by both the email and the id
 * an OWUI user can be traced under. A row whose user matches nobody in the
 * directory keeps its raw value — better an unresolved email than a blank row.
 */
export function userDisplayNames(users: DirectoryUser[]): Map<string, string> {
	const byKey = new Map<string, string>();
	for (const u of users) {
		const name = u.name?.trim();
		if (!name) continue;
		for (const traced of [u.email, u.id]) {
			const k = normalizeUserKey(traced ?? '');
			if (k) byKey.set(k, name);
		}
	}
	return byKey;
}

/**
 * Labels each user with their display name, keeping the raw Langfuse value as
 * `detail` so the UI can still show which account a name stands for.
 */
export function aggregateByUser(rows: MetricRow[], users: DirectoryUser[] = []): Agg[] {
	const names = userDisplayNames(users);
	const nameOf = (r: MetricRow) => names.get(normalizeUserKey(r.user));
	return aggregateBy(
		rows,
		(r) => normalizeUserKey(r.user),
		(r) => nameOf(r) ?? r.user,
		// Only when it adds something the label does not already say.
		(r) => (nameOf(r) ? r.user : undefined)
	);
}

/**
 * Message behind a failed load, or null when the failure carries none.
 *
 * The Langfuse client rethrows the API's `detail` value, which is a string for
 * our own HTTPExceptions but an Error (or anything else) when the request dies
 * before reaching a handler — so the shape is narrowed, never asserted.
 */
export function describeLoadError(e: unknown): string | null {
	const text =
		typeof e === 'string'
			? e
			: e instanceof Error
				? e.message
				: typeof e === 'object' &&
					  e !== null &&
					  typeof (e as { detail?: unknown }).detail === 'string'
					? (e as { detail: string }).detail
					: '';
	return text.trim() || null;
}

export function toBars(aggs: Agg[], limit: number): BarDatum[] {
	const slice = aggs.slice(0, limit);
	const max = slice.reduce((m, a) => Math.max(m, a.cost), 0);
	return slice.map((a) => ({
		...a,
		percent: max <= 0 || a.cost <= 0 ? 0 : Math.round((a.cost / max) * 100)
	}));
}

export function topModel(rows: MetricRow[]): Agg | null {
	return aggregateByModel(rows)[0] ?? null;
}

export function totals(rows: MetricRow[]): { cost: number; tokens: number; observations: number } {
	return rows.reduce(
		(acc, r) => ({
			cost: acc.cost + r.cost,
			tokens: acc.tokens + r.tokens,
			observations: acc.observations + r.observations
		}),
		{ cost: 0, tokens: 0, observations: 0 }
	);
}

/**
 * Accounts that could spend but did not, over accounts that could spend.
 *
 * `pending` is excluded from both sides. Such an account is locked out at
 * `get_verified_user`, so its zero usage is **structural, not behavioural** —
 * counting it as an idle licence overstates what an admin could actually
 * reclaim, and the more unapproved registrations an instance carries, the more
 * it overstates. A missing `role` counts as licensed: the field is optional on
 * the type, and treating unknown as pending would silently shrink the figure.
 */
export function inactiveUsers(
	rows: MetricRow[],
	allUsers: DirectoryUser[]
): { inactive: number; total: number } {
	const active = new Set(rows.map((r) => normalizeUserKey(r.user)));
	const isActive = (u: DirectoryUser) =>
		active.has(normalizeUserKey(u.email)) || active.has(normalizeUserKey(u.id));
	const licensed = allUsers.filter((u) => u.role !== 'pending');
	const inactive = licensed.filter((u) => !isActive(u)).length;
	return { inactive, total: licensed.length };
}
