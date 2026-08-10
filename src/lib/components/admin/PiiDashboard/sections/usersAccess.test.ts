import { describe, it, expect } from 'vitest';
import type { MetricRow } from '$lib/apis/langfuse';
import { totals } from './costAnalytics';
import {
	statusOf,
	costByUser,
	buildRows,
	unattributedCost,
	modelsCountKey,
	type AccessUser
} from './usersAccess';

const row = (user: string, cost: number, model = 'gpt-4', tokens = 10): MetricRow => ({
	user,
	model,
	tokens,
	cost,
	observations: 1
});

/** A public read grant — the simplest way to make a model reachable. */
const pub = { principal_type: 'user', principal_id: '*', permission: 'read' };

const user = (over: Partial<AccessUser> = {}): AccessUser => ({
	id: 'id-1',
	name: 'Ana',
	email: 'ana@x.com',
	role: 'user',
	group_ids: [],
	...over
});

describe('statusOf', () => {
	it('returns pending for a never-approved account', () => {
		expect(statusOf(user({ role: 'pending' }), false)).toBe('pending');
	});

	it('keeps pending ahead of usage, even though that combination cannot occur', () => {
		// A pending account is locked out at get_verified_user, so it can never
		// spend. The hierarchy must still hold if the data ever says otherwise.
		expect(statusOf(user({ role: 'pending' }), true)).toBe('pending');
	});

	it('returns inactive for a user with no usage', () => {
		expect(statusOf(user(), false)).toBe('inactive');
	});

	it('returns active for a user with usage', () => {
		expect(statusOf(user(), true)).toBe('active');
	});

	it('gives admins no immunity from inactive', () => {
		expect(statusOf(user({ role: 'admin' }), false)).toBe('inactive');
		expect(statusOf(user({ role: 'admin' }), true)).toBe('active');
	});
});

describe('costByUser', () => {
	it('sums rows of one identity across models', () => {
		const out = costByUser([row('a@x.com', 0.5, 'gpt-4'), row('a@x.com', 0.25, 'claude')]);
		expect(out.get('a@x.com')).toBe(0.75);
	});

	it('folds case and whitespace variants into one key', () => {
		const out = costByUser([row(' A@X.com ', 0.5), row('a@x.com', 0.25)]);
		expect(out.size).toBe(1);
		expect(out.get('a@x.com')).toBe(0.75);
	});

	it('keeps a refund rather than discarding it', () => {
		expect(costByUser([row('a@x.com', 1), row('a@x.com', -0.25)]).get('a@x.com')).toBe(0.75);
	});

	it('handles no rows', () => {
		expect(costByUser([]).size).toBe(0);
	});
});

describe('buildRows', () => {
	it('attributes a row matched by email', () => {
		const rows = buildRows([user()], [row('ana@x.com', 0.5)]);
		expect(rows[0].cost).toBe(0.5);
		expect(rows[0].status).toBe('active');
	});

	it('attributes a row matched by OWUI id', () => {
		const rows = buildRows([user()], [row('id-1', 0.5)]);
		expect(rows[0].cost).toBe(0.5);
		expect(rows[0].status).toBe('active');
	});

	it('matches email and id to the same outcome', () => {
		const byEmail = buildRows([user()], [row('ana@x.com', 0.5)]);
		const byId = buildRows([user()], [row('id-1', 0.5)]);
		expect(byEmail[0]).toEqual(byId[0]);
	});

	it('matches case- and whitespace-variant identities', () => {
		expect(buildRows([user()], [row('  ANA@X.COM ', 0.5)])[0].cost).toBe(0.5);
	});

	it('leaves a user with no rows at zero cost and inactive', () => {
		const rows = buildRows([user()], [row('someone-else@x.com', 0.5)]);
		expect(rows[0].cost).toBe(0);
		expect(rows[0].status).toBe('inactive');
	});

	it('drops rows that match nobody instead of inventing a row for them', () => {
		const rows = buildRows([user()], [row('ghost@x.com', 0.5), row('(unknown)', 0.25)]);
		expect(rows).toHaveLength(1);
		expect(rows[0].cost).toBe(0);
	});

	it('counts a row for the first claimant when two users would claim it', () => {
		// b's id is a's email — pathological, but it must not double-count.
		const a = user({ id: 'id-a', email: 'shared@x.com', name: 'A' });
		const b = user({ id: 'shared@x.com', email: 'b@x.com', name: 'B' });
		const rows = buildRows([a, b], [row('shared@x.com', 1)]);
		expect(rows[0].cost).toBe(1);
		expect(rows[1].cost).toBe(0);
		expect(rows[0].cost + rows[1].cost).toBe(1);
	});

	it('treats a net-zero refund as usage, not as idleness', () => {
		const rows = buildRows([user()], [row('ana@x.com', 1), row('ana@x.com', -1)]);
		expect(rows[0].cost).toBe(0);
		expect(rows[0].status).toBe('active');
	});

	it('reports pending regardless of attributed spend', () => {
		const rows = buildRows([user({ role: 'pending' })], [row('ana@x.com', 0.5)]);
		expect(rows[0].status).toBe('pending');
	});

	it('reads masking as On when the user never touched the setting', () => {
		// Absent key means enabled, both in the backend and in getPiiMaskingDefault.
		expect(buildRows([user()], [])[0].maskingEnabled).toBe(true);
		expect(buildRows([user({ settings: null })], [])[0].maskingEnabled).toBe(true);
		expect(buildRows([user({ settings: { ui: {} } })], [])[0].maskingEnabled).toBe(true);
	});

	it('reads masking as Off only when the setting says so', () => {
		const off = user({
			settings: { ui: { pipelines: { valves: { pii_filter: { pii_masking_enabled: false } } } } }
		});
		expect(buildRows([off], [])[0].maskingEnabled).toBe(false);
	});

	it('leaves the model fields neutral when no catalogue is supplied', () => {
		const r = buildRows([user()], [])[0];
		expect(r.grantedCount).toBe(0);
		expect(r.allModels).toBe(false);
	});

	it('counts the models a user may read', () => {
		const catalogue = {
			models: [
				{ id: 'public', user_id: 'owner', access_grants: [pub] },
				{ id: 'mine', user_id: 'id-1', access_grants: [] },
				{ id: 'theirs', user_id: 'owner', access_grants: [] }
			],
			truncated: false
		};
		const r = buildRows([user()], [], catalogue)[0];
		expect(r.grantedCount).toBe(2);
		expect(r.allModels).toBe(false);
	});

	it('claims All models only when every registered model is granted', () => {
		const catalogue = {
			models: [
				{ id: 'a', user_id: 'owner', access_grants: [pub] },
				{ id: 'b', user_id: 'owner', access_grants: [pub] }
			],
			truncated: false
		};
		const r = buildRows([user()], [], catalogue)[0];
		expect(r.grantedCount).toBe(2);
		expect(r.allModels).toBe(true);
	});

	it('never claims All models while the catalogue is cut off', () => {
		const catalogue = {
			models: [{ id: 'a', user_id: 'owner', access_grants: [pub] }],
			truncated: true
		};
		const r = buildRows([user()], [], catalogue)[0];
		// Granted equals total here, but the total itself is unknown.
		expect(r.grantedCount).toBe(1);
		expect(r.allModels).toBe(false);
	});

	it('never claims All models over an empty catalogue', () => {
		const r = buildRows([user()], [], { models: [], truncated: false })[0];
		expect(r.grantedCount).toBe(0);
		expect(r.allModels).toBe(false);
	});

	it('handles an empty directory', () => {
		expect(buildRows([], [row('a@x.com', 1)])).toEqual([]);
	});

	it('handles both inputs empty', () => {
		expect(buildRows([], [])).toEqual([]);
	});

	it('handles a directory where every cost is zero', () => {
		const rows = buildRows([user()], [row('ana@x.com', 0, 'gpt-4', 5)]);
		expect(rows[0].cost).toBe(0);
		// The row exists, so the user was seen — zero cost is not idleness.
		expect(rows[0].status).toBe('active');
	});
});

describe('unattributedCost', () => {
	it('sums rows that match no account', () => {
		expect(unattributedCost([row('ghost@x.com', 0.5), row('ana@x.com', 1)], [user()])).toBe(0.5);
	});

	it('counts the (unknown) fallback identity', () => {
		expect(unattributedCost([row('(unknown)', 0.25)], [user()])).toBe(0.25);
	});

	it('returns zero when every row is attributed', () => {
		expect(unattributedCost([row('ana@x.com', 1), row('id-1', 0.5)], [user()])).toBe(0);
	});

	it('keeps a refund in the unattributed total', () => {
		expect(unattributedCost([row('ghost@x.com', -0.5)], [user()])).toBe(-0.5);
	});

	it('treats the whole set as unattributed when the directory is empty', () => {
		expect(unattributedCost([row('a@x.com', 1), row('b@x.com', 0.5)], [])).toBe(1.5);
	});

	it('handles no rows', () => {
		expect(unattributedCost([], [user()])).toBe(0);
	});
});

describe('modelsCountKey', () => {
	it('uses the singular key for exactly one model', () => {
		// The whole reason this function exists: i18next plurals resolve back to
		// the base key under an empty en-US catalogue, so one grant would read
		// "1 models" if the choice were left to t().
		expect(modelsCountKey(1)).toBe('1 model');
	});

	it('uses the plural key for two models', () => {
		expect(modelsCountKey(2)).toBe('{{count}} models');
	});

	it('uses the plural key for a large catalogue', () => {
		expect(modelsCountKey(1000)).toBe('{{count}} models');
	});

	it('uses the plural key at zero, which the cell never renders', () => {
		// English takes the plural at zero. The table shows an em dash instead,
		// so this answer is never on screen — it keeps the function total.
		expect(modelsCountKey(0)).toBe('{{count}} models');
	});
});

describe('reconciliation with section 3', () => {
	/**
	 * The reason the Unattributed row exists: what the table shows must add up to
	 * the Total cost KPI rendered directly above it, for the same window.
	 */
	const reconciles = (metricRows: MetricRow[], users: AccessUser[]) => {
		const attributed = buildRows(users, metricRows).reduce((sum, r) => sum + r.cost, 0);
		return attributed + unattributedCost(metricRows, users);
	};

	it('matches totals().cost for a mixed set', () => {
		const users = [
			user({ id: 'id-a', email: 'a@x.com', name: 'A' }),
			user({ id: 'id-b', email: 'b@x.com', name: 'B' })
		];
		// Binary-exact values so the comparison cannot fail on IEEE-754 drift.
		const rows = [
			row('a@x.com', 0.5),
			row('ID-A', 0.25),
			row('b@x.com', 0.125),
			row('ghost@x.com', 0.0625),
			row('(unknown)', 0.03125)
		];
		expect(reconciles(rows, users)).toBe(totals(rows).cost);
	});

	it('matches totals().cost when nothing is attributed', () => {
		const rows = [row('ghost@x.com', 0.5), row('(unknown)', 0.25)];
		expect(reconciles(rows, [])).toBe(totals(rows).cost);
	});

	it('matches totals().cost when everything is attributed', () => {
		const rows = [row('ana@x.com', 0.5), row('id-1', 0.25)];
		expect(reconciles(rows, [user()])).toBe(totals(rows).cost);
	});

	it('matches totals().cost with refunds in the set', () => {
		const rows = [row('ana@x.com', 1), row('ghost@x.com', -0.5)];
		expect(reconciles(rows, [user()])).toBe(totals(rows).cost);
	});

	it('matches totals().cost when two users could claim one identity', () => {
		const a = user({ id: 'id-a', email: 'shared@x.com', name: 'A' });
		const b = user({ id: 'shared@x.com', email: 'b@x.com', name: 'B' });
		const rows = [row('shared@x.com', 1), row('b@x.com', 0.5)];
		expect(reconciles(rows, [a, b])).toBe(totals(rows).cost);
	});

	it('matches totals().cost on empty input', () => {
		expect(reconciles([], [])).toBe(totals([]).cost);
	});
});
