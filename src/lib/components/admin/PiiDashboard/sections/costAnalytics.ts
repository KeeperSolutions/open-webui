import type { MetricRow } from '$lib/apis/langfuse';

export type Agg = { name: string; tokens: number; cost: number };
export type BarDatum = Agg & { percent: number };

function aggregateBy(rows: MetricRow[], key: (r: MetricRow) => string): Agg[] {
	const map = new Map<string, Agg>();
	for (const r of rows) {
		const name = key(r);
		const existing = map.get(name);
		if (existing) {
			existing.tokens += r.tokens;
			existing.cost += r.cost;
		} else {
			map.set(name, { name, tokens: r.tokens, cost: r.cost });
		}
	}
	return [...map.values()].sort((a, b) => b.cost - a.cost);
}

export function aggregateByModel(rows: MetricRow[]): Agg[] {
	return aggregateBy(rows, (r) => r.model);
}

export function aggregateByUser(rows: MetricRow[]): Agg[] {
	return aggregateBy(rows, (r) => r.user);
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
	const active = new Set(rows.map((r) => (r.user ?? '').trim().toLowerCase()));
	const isActive = (u: { id: string; email: string }) =>
		active.has((u.email ?? '').trim().toLowerCase()) || active.has((u.id ?? '').trim().toLowerCase());
	const inactive = allUsers.filter((u) => !isActive(u)).length;
	return { inactive, total: allUsers.length };
}
