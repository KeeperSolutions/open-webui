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

export function formatCost(c: number): string {
	return '$' + (c === 0 ? c.toFixed(2) : c.toFixed(4));
}

export function stripNonDigits(value: string): number {
	const digits = value.replace(/\D/g, '');
	return digits ? parseInt(digits, 10) : 1;
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
