import { describe, it, expect } from 'vitest';
import type { MetricRow } from './index';
import {
	sortRows,
	totalPages,
	paginateRows,
	totalTokens,
	totalCost,
	formatCost,
	buildCsvLines,
	rowNumber,
	effectivePageSize
} from './tableUtils';

const row = (user: string, model: string, tokens: number, cost: number): MetricRow => ({
	user,
	model,
	tokens,
	cost
});

const ROWS = [
	row('alice@x.com', 'gpt-4', 1000, 0.02),
	row('bob@x.com', 'claude', 500, 0.01),
	row('carol@x.com', 'gpt-3.5', 200, 0.001),
	row('alice@x.com', 'claude', 300, 0.005)
];

// ─── sortRows ────────────────────────────────────────────────────────────────

describe('sortRows', () => {
	it('sorts tokens descending by default (asc=false)', () => {
		const result = sortRows(ROWS, 'tokens', false);
		expect(result[0].tokens).toBe(1000);
		expect(result[result.length - 1].tokens).toBe(200);
	});

	it('sorts tokens ascending', () => {
		const result = sortRows(ROWS, 'tokens', true);
		expect(result[0].tokens).toBe(200);
		expect(result[result.length - 1].tokens).toBe(1000);
	});

	it('sorts cost descending', () => {
		const result = sortRows(ROWS, 'cost', false);
		expect(result[0].cost).toBe(0.02);
	});

	it('sorts user alphabetically ascending', () => {
		const result = sortRows(ROWS, 'user', true);
		expect(result[0].user).toBe('alice@x.com');
		expect(result[result.length - 1].user).toBe('carol@x.com');
	});

	it('sorts user alphabetically descending', () => {
		const result = sortRows(ROWS, 'user', false);
		expect(result[0].user).toBe('carol@x.com');
	});

	it('sorts model alphabetically', () => {
		const result = sortRows(ROWS, 'model', true);
		expect(result[0].model).toBe('claude');
	});

	it('does not mutate original array', () => {
		const original = [...ROWS];
		sortRows(ROWS, 'tokens', true);
		expect(ROWS).toEqual(original);
	});

	it('handles empty array', () => {
		expect(sortRows([], 'tokens', false)).toEqual([]);
	});

	it('handles single row', () => {
		const single = [row('a@b.com', 'gpt-4', 100, 0.01)];
		expect(sortRows(single, 'tokens', false)).toEqual(single);
	});

	it('stable-ish: equal values keep relative order', () => {
		const tied = [row('b@x.com', 'gpt-4', 100, 0.01), row('a@x.com', 'gpt-4', 100, 0.01)];
		const result = sortRows(tied, 'tokens', false);
		expect(result[0].tokens).toBe(100);
		expect(result[1].tokens).toBe(100);
	});
});

// ─── effectivePageSize ───────────────────────────────────────────────────────

describe('effectivePageSize', () => {
	it('returns pageSize when non-zero', () => {
		expect(effectivePageSize(10, 100)).toBe(10);
		expect(effectivePageSize(25, 100)).toBe(25);
	});

	it('returns totalRows when pageSize is 0 (All)', () => {
		expect(effectivePageSize(0, 87)).toBe(87);
	});

	it('returns 0 when pageSize=0 and no rows', () => {
		expect(effectivePageSize(0, 0)).toBe(0);
	});
});

// ─── totalPages ──────────────────────────────────────────────────────────────

describe('totalPages', () => {
	it('returns 1 for empty rows', () => {
		expect(totalPages(0, 10)).toBe(1);
	});

	it('returns 1 when all rows fit on one page', () => {
		expect(totalPages(5, 10)).toBe(1);
	});

	it('returns correct pages for exact multiple', () => {
		expect(totalPages(30, 10)).toBe(3);
	});

	it('rounds up for partial last page', () => {
		expect(totalPages(31, 10)).toBe(4);
	});

	it('returns 1 when pageSize is 0 (All)', () => {
		expect(totalPages(100, 0)).toBe(1);
	});

	it('returns 1 for single row', () => {
		expect(totalPages(1, 25)).toBe(1);
	});

	it('handles very large row count', () => {
		expect(totalPages(10000, 25)).toBe(400);
	});
});

// ─── paginateRows ────────────────────────────────────────────────────────────

describe('paginateRows', () => {
	const rows = Array.from({ length: 55 }, (_, i) =>
		row(`user${i}@x.com`, 'gpt-4', i * 10, i * 0.001)
	);

	it('returns first page correctly', () => {
		const result = paginateRows(rows, 1, 25);
		expect(result).toHaveLength(25);
		expect(result[0].user).toBe('user0@x.com');
	});

	it('returns second page correctly', () => {
		const result = paginateRows(rows, 2, 25);
		expect(result).toHaveLength(25);
		expect(result[0].user).toBe('user25@x.com');
	});

	it('returns partial last page', () => {
		const result = paginateRows(rows, 3, 25);
		expect(result).toHaveLength(5);
	});

	it('returns all rows when pageSize is 0', () => {
		const result = paginateRows(rows, 1, 0);
		expect(result).toHaveLength(55);
	});

	it('returns empty for empty rows', () => {
		expect(paginateRows([], 1, 10)).toEqual([]);
	});

	it('returns empty for out-of-range page', () => {
		expect(paginateRows(rows, 99, 25)).toEqual([]);
	});

	it('page 1 with pageSize larger than rows returns all rows', () => {
		const small = ROWS.slice(0, 3);
		expect(paginateRows(small, 1, 25)).toHaveLength(3);
	});
});

// ─── totalTokens ────────────────────────────────────────────────────────────

describe('totalTokens', () => {
	it('sums tokens correctly', () => {
		expect(totalTokens(ROWS)).toBe(2000);
	});

	it('returns 0 for empty array', () => {
		expect(totalTokens([])).toBe(0);
	});

	it('handles single row', () => {
		expect(totalTokens([row('a@b.com', 'gpt-4', 42, 0.01)])).toBe(42);
	});

	it('handles very large token counts without overflow', () => {
		const big = [row('a@b.com', 'gpt-4', 999_999_999, 0), row('b@b.com', 'gpt-4', 1, 0)];
		expect(totalTokens(big)).toBe(1_000_000_000);
	});
});

// ─── totalCost ───────────────────────────────────────────────────────────────

describe('totalCost', () => {
	it('sums cost correctly', () => {
		expect(totalCost(ROWS)).toBeCloseTo(0.036);
	});

	it('returns 0 for empty array', () => {
		expect(totalCost([])).toBe(0);
	});

	it('handles zero cost rows', () => {
		const zeroRows = [row('a@b.com', 'gpt-4', 100, 0), row('b@b.com', 'gpt-4', 50, 0)];
		expect(totalCost(zeroRows)).toBe(0);
	});
});

// ─── formatCost ─────────────────────────────────────────────────────────────

describe('formatCost', () => {
	it('formats zero as $0.00', () => {
		expect(formatCost(0)).toBe('$0.00');
	});

	it('formats non-zero to 4 decimal places', () => {
		expect(formatCost(0.0234)).toBe('$0.0234');
	});

	it('formats small cost correctly', () => {
		expect(formatCost(0.0001)).toBe('$0.0001');
	});

	it('formats large cost correctly', () => {
		expect(formatCost(123.4567)).toBe('$123.4567');
	});

	it('does not use 4 decimals for zero', () => {
		expect(formatCost(0)).not.toBe('$0.0000');
	});

	it('handles negative cost (refund)', () => {
		expect(formatCost(-0.0050)).toBe('-$0.0050');
	});
});

// ─── buildCsvLines ───────────────────────────────────────────────────────────

describe('buildCsvLines', () => {
	it('first line is header', () => {
		const lines = buildCsvLines(ROWS);
		expect(lines[0]).toBe('User,Model,Tokens,Cost');
	});

	it('has correct number of lines (header + rows)', () => {
		expect(buildCsvLines(ROWS)).toHaveLength(ROWS.length + 1);
	});

	it('produces correct data line', () => {
		const lines = buildCsvLines([row('alice@x.com', 'gpt-4', 1000, 0.02)]);
		expect(lines[1]).toBe('alice@x.com,gpt-4,1000,0.0200');
	});

	it('returns only header for empty rows', () => {
		const lines = buildCsvLines([]);
		expect(lines).toHaveLength(1);
		expect(lines[0]).toBe('User,Model,Tokens,Cost');
	});

	it('escapes commas in user field', () => {
		const lines = buildCsvLines([row('last, first', 'gpt-4', 10, 0.001)]);
		expect(lines[1]).toContain('"last, first"');
	});

	it('escapes double quotes in model name', () => {
		const lines = buildCsvLines([row('a@b.com', 'model"x"', 10, 0.001)]);
		expect(lines[1]).toContain('"model""x"""');
	});

	it('escapes newlines in fields', () => {
		const lines = buildCsvLines([row('a\nb', 'gpt-4', 10, 0.001)]);
		expect(lines[1]).toContain('"a\nb"');
	});

	it('does not escape plain fields', () => {
		const lines = buildCsvLines([row('alice@x.com', 'gpt-4', 100, 0.01)]);
		expect(lines[1]).not.toContain('"');
	});

	it('cost is always 4 decimal places in CSV', () => {
		const lines = buildCsvLines([row('a@b.com', 'gpt-4', 10, 0)]);
		expect(lines[1]).toContain('0.0000');
	});

	it('honours sort order of input rows', () => {
		const sorted = sortRows(ROWS, 'tokens', true);
		const lines = buildCsvLines(sorted);
		// smallest tokens first
		expect(lines[1]).toContain('200');
	});
});

// ─── rowNumber ───────────────────────────────────────────────────────────────

describe('rowNumber', () => {
	it('first row on first page is 1', () => {
		expect(rowNumber(1, 25, 100, 0)).toBe(1);
	});

	it('last row on first page of 25 is 25', () => {
		expect(rowNumber(1, 25, 100, 24)).toBe(25);
	});

	it('first row on second page is 26', () => {
		expect(rowNumber(2, 25, 100, 0)).toBe(26);
	});

	it('correct number with pageSize 10 on page 3', () => {
		expect(rowNumber(3, 10, 100, 0)).toBe(21);
	});

	it('pageSize 0 (All) always starts at 1', () => {
		expect(rowNumber(1, 0, 55, 0)).toBe(1);
		expect(rowNumber(1, 0, 55, 54)).toBe(55);
	});
});
