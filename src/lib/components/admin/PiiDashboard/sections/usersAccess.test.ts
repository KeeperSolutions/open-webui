import { describe, it, expect } from 'vitest';
import type { MetricRow } from '$lib/apis/langfuse';
import { totals } from './costAnalytics';
import {
	statusOf,
	costByUser,
	buildRows,
	unattributedCost,
	modelsCountKey,
	maskingStateOf,
	maskingRank,
	policyGroupsOf,
	rowActionFor,
	type AccessUser,
	type PolicyGroup
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

	const storedOff = {
		ui: { pipelines: { valves: { pii_filter: { pii_masking_enabled: false } } } }
	};
	const storedOn = {
		ui: { pipelines: { valves: { pii_filter: { pii_masking_enabled: true } } } }
	};

	it("reads masking as 'default' when the user never touched the setting", () => {
		// Absent key means the pipeline masks anyway — protected, not at risk.
		expect(buildRows([user()], [])[0].masking).toBe('default');
		expect(buildRows([user({ settings: null })], [])[0].masking).toBe('default');
		expect(buildRows([user({ settings: { ui: {} } })], [])[0].masking).toBe('default');
	});

	it("reads masking as 'on' when the user chose it", () => {
		expect(buildRows([user({ settings: storedOn })], [])[0].masking).toBe('on');
	});

	it("reads masking as 'off' only when the setting says so and no policy applies", () => {
		expect(buildRows([user({ settings: storedOff })], [])[0].masking).toBe('off');
	});

	it('⚠️ never reports off while the policy is enforced', () => {
		// The contradiction this column was rebuilt to remove: a governance table
		// reporting a risk that does not exist, because masking IS on.
		const enforcedButStoredOff = user({ settings: storedOff, pii_masking_enforced: true });
		const r = buildRows([enforcedButStoredOff], [])[0];
		expect(r.masking).toBe('enforced');
		expect(r.masking).not.toBe('off');
		expect(r.enforced).toBe(true);
	});

	it('policy outranks every stored value', () => {
		for (const settings of [undefined, storedOn, storedOff, { ui: {} }]) {
			const r = buildRows([user({ settings, pii_masking_enforced: true })], [])[0];
			expect(r.masking).toBe('enforced');
		}
	});

	it('reports enforced=false when the server did not flag the user', () => {
		expect(buildRows([user()], [])[0].enforced).toBe(false);
		expect(buildRows([user({ pii_masking_enforced: false })], [])[0].enforced).toBe(false);
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

describe('maskingStateOf', () => {
	it('policy wins over anything stored', () => {
		expect(maskingStateOf(true, false)).toBe('enforced');
		expect(maskingStateOf(true, true)).toBe('enforced');
		expect(maskingStateOf(true, 'unset')).toBe('enforced');
	});

	it("maps 'unset' to default, never to off", () => {
		expect(maskingStateOf(false, 'unset')).toBe('default');
		expect(maskingStateOf(false, 'unset')).not.toBe('off');
	});

	it('maps stored booleans straight through when unenforced', () => {
		expect(maskingStateOf(false, true)).toBe('on');
		expect(maskingStateOf(false, false)).toBe('off');
	});

	it('produces off in exactly one combination', () => {
		const combos: [boolean, boolean | 'unset'][] = [
			[true, true],
			[true, false],
			[true, 'unset'],
			[false, true],
			[false, false],
			[false, 'unset']
		];
		const offs = combos.filter(([e, s]) => maskingStateOf(e, s) === 'off');
		expect(offs).toEqual([[false, false]]);
	});
});

describe('maskingRank', () => {
	it('sorts risk first, so ascending surfaces off', () => {
		const order = (['enforced', 'on', 'default', 'off'] as const)
			.slice()
			.sort((a, b) => maskingRank(a) - maskingRank(b));
		expect(order).toEqual(['off', 'default', 'on', 'enforced']);
	});
});

// ---------------------------------------------------------------------------
// D-14 — the row action
// ---------------------------------------------------------------------------

const POLICY: PolicyGroup = { id: 'g1', name: 'Policy' };
const OTHER: PolicyGroup = { id: 'g2', name: 'Legal' };

const actionRow = (enforced: boolean, policyGroupIds: string[] = []) => ({
	enforced,
	policyGroupIds
});

describe('policyGroupsOf', () => {
	it('keeps only groups that carry the key', () => {
		expect(
			policyGroupsOf([
				{ id: 'g1', name: 'Policy', permissions: { chat: { pii_masking_enforced: true } } },
				{ id: 'g2', name: 'Legal', permissions: { chat: { pii_masking_enforced: false } } },
				{ id: 'g3', name: 'Bare', permissions: {} },
				{ id: 'g4', name: 'Null', permissions: null }
			])
		).toEqual([{ id: 'g1', name: 'Policy' }]);
	});

	it('falls back to the id when a group has no name', () => {
		expect(
			policyGroupsOf([{ id: 'g1', permissions: { chat: { pii_masking_enforced: true } } }])
		).toEqual([{ id: 'g1', name: 'g1' }]);
	});
});

describe('rowActionFor — E-1', () => {
	it('offers Enforce when the user is not under policy', () => {
		expect(rowActionFor(actionRow(false), [POLICY])).toEqual({
			kind: 'enforce',
			targets: [POLICY]
		});
	});

	it('offers Remove when exactly one group is the source', () => {
		expect(rowActionFor(actionRow(true, ['g1']), [POLICY, OTHER])).toEqual({
			kind: 'remove',
			group: POLICY
		});
	});

	it('offers NOTHING when the policy also comes from another group', () => {
		// The middle case, and the only one that can lie: `Remove` here would take
		// the user out of one group and leave them enforced by the other, while the
		// label promised an unlock.
		expect(rowActionFor(actionRow(true, ['g1', 'g2']), [POLICY, OTHER])).toEqual({
			kind: 'none',
			via: [POLICY, OTHER]
		});
	});

	it('names an unknown source group by id rather than dropping it', () => {
		// Dropping it would turn a two-source user into a one-source user, and put
		// a Remove button on a row where removal unlocks nothing.
		expect(rowActionFor(actionRow(true, ['g1', 'ghost']), [POLICY])).toEqual({
			kind: 'none',
			via: [POLICY, { id: 'ghost', name: 'ghost' }]
		});
	});

	it('offers nothing, and blames no group, under an instance-wide default', () => {
		expect(rowActionFor(actionRow(true, []), [POLICY])).toEqual({ kind: 'none', via: [] });
	});

	it('never offers Enforce to someone already enforced — the mirror case', () => {
		for (const sources of [[], ['g1'], ['g1', 'g2']]) {
			expect(rowActionFor(actionRow(true, sources), [POLICY, OTHER]).kind).not.toBe('enforce');
		}
	});
});

describe('rowActionFor — E-2', () => {
	it('carries the single destination when exactly one group has the policy', () => {
		const action = rowActionFor(actionRow(false), [POLICY]);
		expect(action).toEqual({ kind: 'enforce', targets: [POLICY] });
	});

	it('carries every candidate when several groups have the policy', () => {
		// The choice is made per call, from these; nothing here remembers one.
		const action = rowActionFor(actionRow(false), [POLICY, OTHER]);
		expect(action.kind === 'enforce' && action.targets).toEqual([POLICY, OTHER]);
	});

	it('carries no destination when no group has the policy', () => {
		// The component disables the action on this; it must never invent a group.
		expect(rowActionFor(actionRow(false), [])).toEqual({ kind: 'enforce', targets: [] });
	});
});

describe('buildRows — policy sources', () => {
	it('carries the enforcing group ids onto the row', () => {
		const r = buildRows(
			[user({ pii_masking_enforced: true, pii_policy_group_ids: ['g1'] })],
			[]
		)[0];
		expect(r.policyGroupIds).toEqual(['g1']);
	});

	it('defaults to none when the backend does not send the field', () => {
		expect(buildRows([user({ pii_masking_enforced: true })], [])[0].policyGroupIds).toEqual([]);
	});
});
