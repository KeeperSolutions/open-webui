import type { MetricRow } from '$lib/apis/langfuse';

export type Agg = { name: string; tokens: number; cost: number };
export type BarDatum = Agg & { percent: number };

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
	label: (r: MetricRow) => string = key
): Agg[] {
	const map = new Map<string, Agg>();
	for (const r of rows) {
		const k = key(r);
		const existing = map.get(k);
		if (existing) {
			existing.tokens += r.tokens;
			existing.cost += r.cost;
		} else {
			map.set(k, { name: label(r), tokens: r.tokens, cost: r.cost });
		}
	}
	return [...map.values()].sort((a, b) => b.cost - a.cost);
}

export function aggregateByModel(rows: MetricRow[]): Agg[] {
	return aggregateBy(rows, (r) => r.model);
}

export function aggregateByUser(rows: MetricRow[]): Agg[] {
	return aggregateBy(
		rows,
		(r) => normalizeUserKey(r.user),
		(r) => r.user
	);
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

export function inactiveUsers(
	rows: MetricRow[],
	allUsers: { id: string; email: string }[]
): { inactive: number; total: number } {
	const active = new Set(rows.map((r) => normalizeUserKey(r.user)));
	const isActive = (u: { id: string; email: string }) =>
		active.has(normalizeUserKey(u.email)) || active.has(normalizeUserKey(u.id));
	const inactive = allUsers.filter((u) => !isActive(u)).length;
	return { inactive, total: allUsers.length };
}
