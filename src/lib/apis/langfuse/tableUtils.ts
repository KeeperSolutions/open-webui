import type { MetricRow } from './index';

export type SortKey = 'user' | 'model' | 'tokens' | 'cost';

export function sortRows(rows: MetricRow[], key: SortKey, asc: boolean): MetricRow[] {
	return [...rows].sort((a, b) => {
		const mul = asc ? 1 : -1;
		if (key === 'user' || key === 'model') {
			return mul * a[key].localeCompare(b[key]);
		}
		return mul * (a[key] - b[key]);
	});
}

export function effectivePageSize(pageSize: number, totalRows: number): number {
	return pageSize === 0 ? totalRows : pageSize;
}

export function totalPages(rowCount: number, pageSize: number): number {
	const effective = effectivePageSize(pageSize, rowCount);
	if (effective === 0) return 1;
	return Math.max(1, Math.ceil(rowCount / effective));
}

export function paginateRows(rows: MetricRow[], page: number, pageSize: number): MetricRow[] {
	const effective = effectivePageSize(pageSize, rows.length);
	return rows.slice((page - 1) * effective, page * effective);
}

export function totalTokens(rows: MetricRow[]): number {
	return rows.reduce((s, r) => s + r.tokens, 0);
}

export function totalCost(rows: MetricRow[]): number {
	return rows.reduce((s, r) => s + r.cost, 0);
}

export function formatCost(c: number, decimals?: number): string {
	const abs = Math.abs(c);
	const dp = decimals ?? (abs === 0 ? 2 : 4);
	const formatted = '$' + abs.toFixed(dp);
	return c < 0 ? '-' + formatted : formatted;
}

/**
 * Cost display for the admin dashboard: plain currency, two decimals, for every
 * figure on the page. Lives in one place rather than as a decimal count passed
 * at each call site — which is how the same cost ended up rendered two ways.
 *
 * Two decimals means usage below half a cent reads as $0.00 next to a drawn bar.
 * That is accepted: the dashboard answers "what is this costing us", a question
 * whose unit is the dollar, and the alternative — widening precision only for
 * small values — puts a six-decimal row beside a two-decimal one in the same
 * right-aligned column.
 */
export function formatCostDisplay(c: number): string {
	return new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 2
	}).format(c);
}

export function buildCsvLines(rows: MetricRow[]): string[] {
	const escapeCell = (v: string | number): string => {
		const s = String(v);
		return s.includes(',') || s.includes('"') || s.includes('\n')
			? `"${s.replace(/"/g, '""')}"`
			: s;
	};
	return [
		['User', 'Model', 'Tokens', 'Cost'].join(','),
		...rows.map((r) =>
			[r.user, r.model, r.tokens, r.cost.toFixed(4)].map(escapeCell).join(',')
		)
	];
}

export function rowNumber(page: number, pageSize: number, totalRows: number, index: number): number {
	const effective = effectivePageSize(pageSize, totalRows);
	return (page - 1) * effective + index + 1;
}
