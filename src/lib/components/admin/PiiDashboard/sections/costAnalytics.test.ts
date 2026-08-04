import { describe, it, expect } from 'vitest';
import type { MetricRow } from '$lib/apis/langfuse';
import {
	aggregateByModel,
	aggregateByUser,
	toBars,
	topModel,
	totals,
	inactiveUsers
} from './costAnalytics';

const row = (user: string, model: string, tokens: number, cost: number, observations = 1): MetricRow => ({
	user,
	model,
	tokens,
	cost,
	observations,
	traces: 0
});

describe('aggregateByModel', () => {
	it('sums per model and sorts by cost desc', () => {
		const out = aggregateByModel([row('a', 'gpt', 10, 0.2), row('b', 'gpt', 5, 0.3), row('a', 'claude', 1, 0.9)]);
		expect(out).toEqual([
			{ name: 'claude', tokens: 1, cost: 0.9 },
			{ name: 'gpt', tokens: 15, cost: 0.5 }
		]);
	});
	it('handles empty input', () => {
		expect(aggregateByModel([])).toEqual([]);
	});
});

describe('aggregateByUser', () => {
	it('sums per user and sorts by cost desc', () => {
		const out = aggregateByUser([row('a@x', 'm', 10, 0.5), row('b@x', 'm', 5, 0.8), row('a@x', 'm', 1, 0.25)]);
		expect(out).toEqual([
			{ name: 'b@x', tokens: 5, cost: 0.8 },
			{ name: 'a@x', tokens: 11, cost: 0.75 }
		]);
	});
	it('groups case/whitespace variants of one user into a single bar', () => {
		const out = aggregateByUser([row('A@x.com', 'm', 10, 0.5), row(' a@x.com ', 'm', 5, 0.25)]);
		expect(out).toEqual([{ name: 'A@x.com', tokens: 15, cost: 0.75 }]);
	});
	it('labels the bar with the original casing of the first row, not the key', () => {
		expect(aggregateByUser([row('Fiona@X.com', 'm', 1, 0.1)])[0].name).toBe('Fiona@X.com');
	});
});

describe('toBars', () => {
	it('normalises to the slice max and caps to limit', () => {
		const aggs = [
			{ name: 'a', tokens: 0, cost: 1.0 },
			{ name: 'b', tokens: 0, cost: 0.5 },
			{ name: 'c', tokens: 0, cost: 0.25 }
		];
		expect(toBars(aggs, 2)).toEqual([
			{ name: 'a', tokens: 0, cost: 1.0, percent: 100 },
			{ name: 'b', tokens: 0, cost: 0.5, percent: 50 }
		]);
	});
	it('returns 0 percent when all costs are zero', () => {
		expect(toBars([{ name: 'a', tokens: 0, cost: 0 }], 5)).toEqual([{ name: 'a', tokens: 0, cost: 0, percent: 0 }]);
	});
	it('clamps negative cost to 0 percent', () => {
		const out = toBars([{ name: 'a', tokens: 0, cost: 1 }, { name: 'b', tokens: 0, cost: -0.5 }], 5);
		expect(out[1].percent).toBe(0);
	});
	it('tolerates fewer rows than the limit', () => {
		expect(toBars([{ name: 'a', tokens: 0, cost: 1 }], 5)).toHaveLength(1);
	});
});

describe('topModel', () => {
	it('returns the highest-cost model', () => {
		expect(topModel([row('a', 'x', 1, 0.1), row('a', 'y', 1, 0.9)])).toEqual({ name: 'y', tokens: 1, cost: 0.9 });
	});
	it('returns null for empty', () => {
		expect(topModel([])).toBeNull();
	});
});

describe('totals', () => {
	it('sums cost, tokens, observations', () => {
		expect(totals([row('a', 'x', 10, 0.2, 3), row('b', 'y', 5, 0.3, 4)])).toEqual({
			cost: 0.5,
			tokens: 15,
			observations: 7
		});
	});
});

describe('inactiveUsers', () => {
	const users = [
		{ id: 'id-1', email: 'a@x.com' },
		{ id: 'id-2', email: 'b@x.com' },
		{ id: 'id-3', email: 'c@x.com' }
	];
	it('matches active users by email (case-insensitive)', () => {
		const out = inactiveUsers([row('A@X.COM', 'm', 1, 0.1)], users);
		expect(out).toEqual({ inactive: 2, total: 3 });
	});
	it('matches active users by id', () => {
		const out = inactiveUsers([row('id-2', 'm', 1, 0.1)], users);
		expect(out).toEqual({ inactive: 2, total: 3 });
	});
	it('never goes negative for unknown Langfuse users', () => {
		const out = inactiveUsers([row('ghost@x.com', 'm', 1, 0.1), row('(unknown)', 'm', 1, 0.1)], users);
		expect(out).toEqual({ inactive: 3, total: 3 });
	});
	it('handles no users', () => {
		expect(inactiveUsers([row('a@x', 'm', 1, 0.1)], [])).toEqual({ inactive: 0, total: 0 });
	});
});
