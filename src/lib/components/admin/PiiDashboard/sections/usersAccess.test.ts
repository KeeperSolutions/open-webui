import { describe, it, expect, vi } from 'vitest';
import type { MetricRow } from '$lib/apis/langfuse';
import { totals } from './costAnalytics';
import {
	mayActFor,
	statusOf,
	costByUser,
	buildRows,
	unattributedCost,
	modelsCountKey,
	maskingStateOf,
	maskingRank,
	pageOf,
	pageRange,
	ROWS_PER_PAGE,
	policyGroupsOf,
	enforcesMasking,
	enforceTargetsOf,
	rowActionFor,
	type AccessUser,
	type PolicyGroup,
	teamOnlyPolicyGroupCount,
	grantsOnlyMasking,
	broadPolicyGroupCount
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

	it('never reports off while the policy is enforced', () => {
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
// The row action
// ---------------------------------------------------------------------------

const POLICY: PolicyGroup = { id: 'g1', name: 'Policy', isTeamGroup: false };
const OTHER: PolicyGroup = { id: 'g2', name: 'Legal', isTeamGroup: false };
const TEAM_GROUP: PolicyGroup = {
	id: 'g-team',
	name: 'PII \u2014 Acme \u00b7 t1',
	isTeamGroup: true
};

/**
 * Group lists where naming and destinations are the same set. Tests that need
 * the two to differ pass `{ naming, targets }` explicitly.
 */
const lists = (gs: PolicyGroup[]) => ({ naming: gs, targets: gs });

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
		).toEqual([{ id: 'g1', name: 'Policy', isTeamGroup: false }]);
	});

	it('reports a nameless group as unnamed rather than as its id', () => {
		// `null` lets the component render a sentence; an id would be shown verbatim
		// as the group name in the removal dialog.
		expect(
			policyGroupsOf([{ id: 'g1', permissions: { chat: { pii_masking_enforced: true } } }])
		).toEqual([{ id: 'g1', name: null, isTeamGroup: false }]);
	});
});

describe('rowActionFor — more than one source', () => {
	it('offers Enforce when the user is not under policy', () => {
		expect(rowActionFor(actionRow(false), lists([POLICY]))).toEqual({
			kind: 'enforce',
			targets: [POLICY]
		});
	});

	it('offers Remove when exactly one group is the source', () => {
		expect(rowActionFor(actionRow(true, ['g1']), lists([POLICY, OTHER]))).toEqual({
			kind: 'remove',
			group: POLICY
		});
	});

	it('offers NOTHING when the policy also comes from another group', () => {
		// The middle case, and the only one that can lie: `Remove` here would take
		// the user out of one group and leave them enforced by the other, while the
		// label promised an unlock.
		expect(rowActionFor(actionRow(true, ['g1', 'g2']), lists([POLICY, OTHER]))).toEqual({
			kind: 'none',
			via: [POLICY, OTHER]
		});
	});

	it('counts an unknown source group without printing its id', () => {
		// Dropping it would turn a two-source user into a one-source user, and put
		// a Remove button on a row where removal unlocks nothing. It is also not
		// named by its id: see `namedGroup`.
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		expect(rowActionFor(actionRow(true, ['g1', 'ghost']), lists([POLICY]))).toEqual({
			kind: 'none',
			via: [POLICY, { id: 'ghost', name: null, isTeamGroup: false }]
		});
		// The unknown group is logged so the mismatch is visible.
		expect(warn).toHaveBeenCalledOnce();
		warn.mockRestore();
	});

	it('offers nothing, and blames no group, under an instance-wide default', () => {
		expect(rowActionFor(actionRow(true, []), lists([POLICY]))).toEqual({ kind: 'none', via: [] });
	});

	it('never offers Enforce to someone already enforced — the mirror case', () => {
		for (const sources of [[], ['g1'], ['g1', 'g2']]) {
			expect(rowActionFor(actionRow(true, sources), lists([POLICY, OTHER])).kind).not.toBe(
				'enforce'
			);
		}
	});
});

describe('mayActFor', () => {
	it('lets an administrator act', () => {
		expect(mayActFor('admin')).toBe(true);
	});

	it('does not let anyone else act', () => {
		for (const role of ['user', 'pending', '', undefined, null]) {
			expect(mayActFor(role)).toBe(false);
		}
	});

	it('takes the role and nothing else', () => {
		// With a single parameter, the address the viewer arrived at cannot enter
		// the decision. Adding a parameter fails this assertion.
		expect(mayActFor.length).toBe(1);
	});
});

describe('rowActionFor — a viewer who may not act', () => {
	it('offers an em dash, not a claim, on a row that is not enforced', () => {
		// Not `{ kind: 'none', via: [] }`: the component renders an empty `via` as
		// "Enforced instance-wide", which would be false for an unmasked person.
		expect(
			rowActionFor(actionRow(false), lists([POLICY]), {
				mayAct: false,
				teamGroupId: null,
				mayManagePolicy: false
			})
		).toEqual({ kind: 'readonly' });
	});

	it('says a source exists outside the team, and names nothing', () => {
		expect(
			rowActionFor(actionRow(true, ['g1']), lists([POLICY]), {
				mayAct: false,
				teamGroupId: null,
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'masked-elsewhere'
		});
	});

	it('gives the same answer for two sources as for one', () => {
		// The number of enforcing groups is not disclosed to a non-admin either.
		expect(
			rowActionFor(actionRow(true, ['g1', 'g2']), lists([POLICY, OTHER]), {
				mayAct: false,
				teamGroupId: null,
				mayManagePolicy: false
			})
		).toEqual(
			rowActionFor(actionRow(true, ['g1']), lists([POLICY]), {
				mayAct: false,
				teamGroupId: null,
				mayManagePolicy: false
			})
		);
	});

	it('carries no group data at all, checked over the serialised value', () => {
		// Checked over the whole serialisation so that any added field carrying a
		// group name or id fails, not only the fields listed here.
		const serialised = JSON.stringify(
			rowActionFor(actionRow(true, ['g1', 'ghost']), lists([POLICY]), {
				mayAct: false,
				teamGroupId: null,
				mayManagePolicy: false
			})
		);
		expect(serialised).not.toContain('g1');
		expect(serialised).not.toContain('ghost');
		expect(serialised).not.toContain(POLICY.name);
		expect(JSON.parse(serialised)).toEqual({ kind: 'masked-elsewhere' });
	});

	it('does not leak a group id even when the group list cannot name it', () => {
		// An unknown group has no name, so only its id could leak here.
		const action = rowActionFor(actionRow(true, ['ghost']), lists([]), {
			mayAct: false,
			teamGroupId: null,
			mayManagePolicy: false
		});
		expect(JSON.stringify(action)).not.toContain('ghost');
	});

	it('leaves the instance-wide default exactly as it was', () => {
		// It names no group, so nothing about it needs hiding.
		expect(
			rowActionFor(actionRow(true, []), lists([POLICY]), {
				mayAct: false,
				teamGroupId: null,
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'none',
			via: []
		});
	});

	it('changes nothing for a viewer who may act', () => {
		for (const sources of [[], ['g1'], ['g1', 'g2']]) {
			expect(
				rowActionFor(actionRow(true, sources), lists([POLICY, OTHER]), {
					mayAct: true,
					teamGroupId: null,
					mayManagePolicy: false
				})
			).toEqual(rowActionFor(actionRow(true, sources), lists([POLICY, OTHER])));
		}
		expect(
			rowActionFor(actionRow(false), lists([POLICY]), {
				mayAct: true,
				teamGroupId: null,
				mayManagePolicy: false
			})
		).toEqual(rowActionFor(actionRow(false), lists([POLICY])));
	});
});

describe('rowActionFor — destinations', () => {
	it('carries the single destination when exactly one group has the policy', () => {
		const action = rowActionFor(actionRow(false), lists([POLICY]));
		expect(action).toEqual({ kind: 'enforce', targets: [POLICY] });
	});

	it('carries every candidate when several groups have the policy', () => {
		// The choice is made per call, from these; nothing here remembers one.
		const action = rowActionFor(actionRow(false), lists([POLICY, OTHER]));
		expect(action.kind === 'enforce' && action.targets).toEqual([POLICY, OTHER]);
	});

	it('carries no destination when no group has the policy', () => {
		// The component disables the action on this; it must never invent a group.
		expect(rowActionFor(actionRow(false), lists([]))).toEqual({ kind: 'enforce', targets: [] });
	});
});

describe('buildRows — policy sources', () => {
	it("carries the server's masked-elsewhere answer onto the row", () => {
		/**
		 * The `rowActionFor` tests build rows directly, so only this test covers the
		 * mapping. Without the field, a team owner would be told that removing
		 * someone lets them turn masking off while another group still masks them.
		 */
		expect(buildRows([user({ masked_by_other_policy: true })], [])[0].maskedByOtherPolicy).toBe(
			true
		);
		expect(buildRows([user({ masked_by_other_policy: false })], [])[0].maskedByOtherPolicy).toBe(
			false
		);
		// Absent reads as `false`; the id scan in `rowActionFor` answers instead.
		expect(buildRows([user()], [])[0].maskedByOtherPolicy).toBe(false);
	});

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

describe('pageOf', () => {
	const list = Array.from({ length: 63 }, (_, i) => i);

	it('returns the first page by default', () => {
		expect(pageOf(list, 1, 25)).toEqual(list.slice(0, 25));
	});

	it('returns the middle page', () => {
		expect(pageOf(list, 2, 25)).toEqual(list.slice(25, 50));
	});

	it('returns a short last page rather than padding it', () => {
		expect(pageOf(list, 3, 25)).toEqual(list.slice(50, 63));
	});

	it('never drops or duplicates a row across the pages', () => {
		const pages = [1, 2, 3].flatMap((p) => pageOf(list, p, 25));
		expect(pages).toEqual(list);
	});

	it('clamps a page past the end to the last one', () => {
		// A page number outlives the list it indexed: sorting changes, and a policy
		// action reloads the section. Rendering nothing would read as "no users".
		expect(pageOf(list, 99, 25)).toEqual(list.slice(50, 63));
	});

	it('clamps a page below one', () => {
		expect(pageOf(list, 0, 25)).toEqual(list.slice(0, 25));
		expect(pageOf(list, -3, 25)).toEqual(list.slice(0, 25));
	});

	it('leaves a list shorter than a page whole', () => {
		expect(pageOf([1, 2, 3], 1, 25)).toEqual([1, 2, 3]);
	});

	it('returns nothing for an empty list instead of throwing', () => {
		expect(pageOf([], 1, 25)).toEqual([]);
	});

	it('falls back to the whole list when the page size is not positive', () => {
		expect(pageOf(list, 2, 0)).toEqual(list);
	});

	it('defaults to the page size the table uses', () => {
		expect(pageOf(list, 1)).toEqual(list.slice(0, ROWS_PER_PAGE));
	});
});

describe('pageRange', () => {
	it('describes the first page', () => {
		expect(pageRange(57, 1, 10)).toEqual({ from: 1, to: 10 });
	});

	it('describes a middle page', () => {
		expect(pageRange(57, 3, 10)).toEqual({ from: 21, to: 30 });
	});

	it('ends a short last page where the list ends', () => {
		// Not `page * perPage`: the summary would claim rows 51-60 of a list that
		// stops at 57.
		expect(pageRange(57, 6, 10)).toEqual({ from: 51, to: 57 });
	});

	it('agrees with the slice pageOf returns, on every page', () => {
		// The invariant that matters. A summary that disagrees with the rows under
		// it is worse than no summary, so the two are checked against each other
		// rather than against hand-written numbers.
		const rows = Array.from({ length: 63 }, (_, i) => i);
		for (const page of [1, 2, 3, 4, 99, 0, -1]) {
			const { from, to } = pageRange(rows.length, page, 25);
			expect(rows.slice(from - 1, to)).toEqual(pageOf(rows, page, 25));
		}
	});

	it('clamps a page past the end to the last one', () => {
		expect(pageRange(57, 99, 10)).toEqual({ from: 51, to: 57 });
	});

	it('clamps a page below one', () => {
		expect(pageRange(57, 0, 10)).toEqual({ from: 1, to: 10 });
		expect(pageRange(57, -3, 10)).toEqual({ from: 1, to: 10 });
	});

	it('covers a list shorter than one page whole', () => {
		expect(pageRange(3, 1, 10)).toEqual({ from: 1, to: 3 });
	});

	it('reports an empty range for an empty list instead of throwing', () => {
		expect(pageRange(0, 1, 10)).toEqual({ from: 1, to: 0 });
	});

	it('covers the whole list when the page size is not positive', () => {
		expect(pageRange(57, 2, 0)).toEqual({ from: 1, to: 57 });
	});

	it('defaults to the page size the table uses', () => {
		expect(pageRange(57, 1)).toEqual({ from: 1, to: ROWS_PER_PAGE });
	});
});

describe('policyGroupsOf — a team group is not an enforce destination', () => {
	const enforcing = (id: string, over = {}) => ({
		id,
		name: id,
		permissions: { chat: { pii_masking_enforced: true } },
		...over
	});

	it('excludes groups a team owns', () => {
		const out = enforceTargetsOf([
			enforcing('custom'),
			enforcing('team-a', { is_team_group: true })
		]);
		expect(out.map((g) => g.id)).toEqual(['custom']);
	});

	it('but policyGroupsOf keeps them, because naming is not a destination', () => {
		/**
		 * The naming list must include team groups, so an admin's `Remove` dialog
		 * for a team member shows the group's name instead of its raw id.
		 */
		const out = policyGroupsOf([enforcing('custom'), enforcing('team-a', { is_team_group: true })]);
		expect(out.map((g) => g.id)).toEqual(['custom', 'team-a']);
		expect(out.find((g) => g.id === 'team-a')?.isTeamGroup).toBe(true);
	});

	it('keeps custom groups that enforce', () => {
		expect(policyGroupsOf([enforcing('custom')]).map((g) => g.id)).toEqual(['custom']);
	});

	it('keeps a group the backend explicitly marked as not a team group', () => {
		/**
		 * `GET /groups/` sends `is_team_group: false` for every ordinary group. A
		 * filter written as `=== undefined` would pass the other tests here and drop
		 * every destination in production.
		 */
		expect(
			policyGroupsOf([enforcing('custom', { is_team_group: false })]).map((g) => g.id)
		).toEqual(['custom']);
	});

	it('treats a missing flag as "not a team group"', () => {
		// Treating it as a team group would hide every destination for a payload
		// that predates the field.
		expect(policyGroupsOf([enforcing('older-payload')]).map((g) => g.id)).toEqual([
			'older-payload'
		]);
	});

	it('still ignores groups that do not enforce at all', () => {
		expect(policyGroupsOf([{ id: 'plain', name: 'plain', permissions: {} }])).toEqual([]);
	});
});

describe('grantsOnlyMasking', () => {
	it('accepts a group whose only permission is masking', () => {
		expect(grantsOnlyMasking({ chat: { pii_masking_enforced: true } })).toBe(true);
	});

	it('accepts one carrying other permissions that are OFF', () => {
		// The stored tree is mostly `false` leaves, so counting keys instead of
		// values would reject every real group.
		expect(
			grantsOnlyMasking({
				chat: { pii_masking_enforced: true, file_upload: false, temporary_enforced: false },
				features: { web_search: false }
			})
		).toBe(true);
	});

	it('rejects one that also switches something else on', () => {
		expect(
			grantsOnlyMasking({ chat: { pii_masking_enforced: true }, features: { web_search: true } })
		).toBe(false);
	});

	it('rejects one that grants something else in the same branch', () => {
		expect(grantsOnlyMasking({ chat: { pii_masking_enforced: true, file_upload: true } })).toBe(
			false
		);
	});

	it('rejects a group that grants nothing at all', () => {
		expect(grantsOnlyMasking({})).toBe(false);
		expect(grantsOnlyMasking(null)).toBe(false);
		expect(grantsOnlyMasking(undefined)).toBe(false);
	});

	it('rejects one that grants only something else', () => {
		expect(grantsOnlyMasking({ features: { web_search: true } })).toBe(false);
	});

	it('counts a truthy non-boolean as a grant, because the server does', () => {
		/**
		 * The server ends permission checks in `bool(...)`
		 * (`utils/access_control/__init__.py`) and does not validate leaves, so `1`
		 * is a real grant. Missing it would let `Enforce` hand over that capability.
		 * A group with a numeric setting is therefore not a destination, and the
		 * empty state explains why.
		 */
		expect(grantsOnlyMasking({ chat: { pii_masking_enforced: true }, limits: { seats: 5 } })).toBe(
			false
		);
		expect(grantsOnlyMasking({ chat: { pii_masking_enforced: true, web_search: 1 } })).toBe(false);
		expect(grantsOnlyMasking({ chat: { pii_masking_enforced: true, model: 'gpt' } })).toBe(false);
	});

	it('still ignores every falsy leaf, whatever its type', () => {
		// `bool(0)`, `bool("")` and `bool(None)` are all False on the server too.
		expect(
			grantsOnlyMasking({
				chat: { pii_masking_enforced: true, web_search: 0, note: '', other: null }
			})
		).toBe(true);
	});

	it('treats a truthy masking flag as enforcing, matching the server', () => {
		// `group_enforces_pii_masking` ends in `bool(node)` (`utils/pii_policy.py`),
		// so a group carrying `1` masks people and must be named as their source.
		expect(enforcesMasking({ chat: { pii_masking_enforced: 1 } })).toBe(true);
		expect(enforcesMasking({ chat: { pii_masking_enforced: 0 } })).toBe(false);
	});
});

describe('enforceTargetsOf — a group that grants more is not a destination', () => {
	const masking = { chat: { pii_masking_enforced: true } };
	const broad = { chat: { pii_masking_enforced: true }, features: { web_search: true } };

	it('offers only the single-purpose group', () => {
		const out = enforceTargetsOf([
			{ id: 'dedicated', name: 'PII Masking Policy', permissions: masking },
			{ id: 'wide', name: 'Proba spajanja', permissions: broad }
		]);
		expect(out.map((g) => g.id)).toEqual(['dedicated']);
	});

	it('but policyGroupsOf keeps the broad one, because naming is not a destination', () => {
		/**
		 * Someone masked by the broad group must still be named in the admin's
		 * Remove dialog, so the destination filter must not apply to naming.
		 */
		const out = policyGroupsOf([
			{ id: 'dedicated', name: 'PII Masking Policy', permissions: masking },
			{ id: 'wide', name: 'Proba spajanja', permissions: broad }
		]);
		expect(out.map((g) => g.id)).toEqual(['dedicated', 'wide']);
	});

	it('can leave the list empty, which is a real state', () => {
		expect(enforceTargetsOf([{ id: 'wide', permissions: broad }])).toEqual([]);
	});

	it('still excludes a team group even when it grants only masking', () => {
		// The two exclusions are independent; neither may shadow the other.
		expect(enforceTargetsOf([{ id: 'team', permissions: masking, is_team_group: true }])).toEqual(
			[]
		);
	});
});

describe('broadPolicyGroupCount — the third reason the list is empty', () => {
	const masking = { chat: { pii_masking_enforced: true } };
	const broad = { chat: { pii_masking_enforced: true }, features: { web_search: true } };

	it('counts enforcing groups excluded for granting more', () => {
		expect(broadPolicyGroupCount([{ id: 'w', permissions: broad }])).toBe(1);
	});

	it('does not count the single-purpose one', () => {
		expect(broadPolicyGroupCount([{ id: 'd', permissions: masking }])).toBe(0);
	});

	it('does not count a group that does not enforce at all', () => {
		expect(
			broadPolicyGroupCount([{ id: 'p', permissions: { features: { web_search: true } } }])
		).toBe(0);
	});

	it('does not count a team group — that cause has its own counter', () => {
		expect(broadPolicyGroupCount([{ id: 't', permissions: broad, is_team_group: true }])).toBe(0);
	});
});

describe('teamOnlyPolicyGroupCount — why the destination list is empty', () => {
	const enforcing = (id: string, over = {}) => ({
		id,
		name: id,
		permissions: { chat: { pii_masking_enforced: true } },
		...over
	});

	it('counts enforcing team groups', () => {
		expect(
			teamOnlyPolicyGroupCount([
				enforcing('t1', { is_team_group: true }),
				enforcing('t2', { is_team_group: true }),
				enforcing('custom')
			])
		).toBe(2);
	});

	it('is zero when nothing enforces', () => {
		expect(teamOnlyPolicyGroupCount([{ id: 'plain', permissions: {} }])).toBe(0);
	});

	it('does not count a team group that does not enforce', () => {
		// The count answers "was a destination excluded", and a non-enforcing group
		// was never a destination.
		expect(teamOnlyPolicyGroupCount([{ id: 't', permissions: {}, is_team_group: true }])).toBe(0);
	});
});

describe('rowActionFor — masking that comes from the viewer’s own team', () => {
	const enforced = (ids: string[]) => ({ enforced: true, policyGroupIds: ids });
	const TEAM = 'g-team';
	const OTHER = 'g-other';

	it('names the team policy when the team group is the source', () => {
		expect(
			rowActionFor(enforced([TEAM]), lists([]), {
				mayAct: false,
				teamGroupId: TEAM,
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'masked-team',
			teamGroupId: TEAM
		});
	});

	it('still says "outside the team" when the source is another group', () => {
		expect(
			rowActionFor(enforced([OTHER]), lists([]), {
				mayAct: false,
				teamGroupId: TEAM,
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'masked-elsewhere'
		});
	});

	it('says team policy — and nothing else — when both are sources', () => {
		/**
		 * Mentioning the other source would disclose that a source outside the
		 * owner's reach exists.
		 */
		expect(
			rowActionFor(enforced([TEAM, OTHER]), lists([]), {
				mayAct: false,
				teamGroupId: TEAM,
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'masked-team',
			teamGroupId: TEAM
		});
	});

	it('falls back to "outside the team" when the team has no group yet', () => {
		// A team whose policy group has not been created yet.
		expect(
			rowActionFor(enforced([OTHER]), lists([]), {
				mayAct: false,
				teamGroupId: null,
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'masked-elsewhere'
		});
	});

	it('is still an em dash for someone who is not masked at all', () => {
		expect(
			rowActionFor({ enforced: false, policyGroupIds: [] }, lists([]), {
				mayAct: false,
				teamGroupId: TEAM,
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'readonly'
		});
	});

	it('carries the team id and nothing else', () => {
		const action = rowActionFor(enforced([TEAM, OTHER]), lists([]), {
			mayAct: false,
			teamGroupId: TEAM,
			mayManagePolicy: false
		});
		expect(Object.keys(action).sort()).toEqual(['kind', 'teamGroupId']);
	});

	it('changes nothing for a viewer who may act', () => {
		// `mayAct` comes from the role; the team id must not affect the admin branch.
		expect(
			rowActionFor({ enforced: false, policyGroupIds: [] }, lists([]), {
				mayAct: true,
				teamGroupId: TEAM,
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'enforce',
			targets: []
		});
	});

	// No arity assertion here, unlike `mayActFor`: the team id is an input by
	// design. The payload is covered by "carries the team id and nothing else".
});

// ---------------------------------------------------------------------------
// An admin acting on somebody else's team member
// ---------------------------------------------------------------------------

describe('rowActionFor — naming is not a destination', () => {
	const teamMember = { enforced: true, policyGroupIds: ['g-team'] };
	// What the loader produces: the team group can be named but not targeted.
	const split = { naming: [POLICY, TEAM_GROUP], targets: [POLICY] };

	it('names the team group instead of falling back to its id', () => {
		/**
		 * The team group is only in `naming`, not `targets`. The action must still
		 * carry its name, or the removal dialog would show a raw id.
		 */
		expect(rowActionFor(teamMember, split)).toEqual({
			kind: 'remove',
			group: TEAM_GROUP
		});
	});

	it('still offers Remove — an admin is not barred from a team policy', () => {
		// An admin may take someone out of a team's policy group; the button only
		// has to name the group correctly.
		expect(rowActionFor(teamMember, split).kind).toBe('remove');
	});

	it('does not offer the team group as an Enforce destination', () => {
		const action = rowActionFor({ enforced: false, policyGroupIds: [] }, split);
		expect(action).toEqual({ kind: 'enforce', targets: [POLICY] });
		expect(JSON.stringify(action)).not.toContain('g-team');
	});

	it('marks which named group belongs to a team', () => {
		const action = rowActionFor(teamMember, split);
		expect(action.kind === 'remove' && action.group.isTeamGroup).toBe(true);
	});

	it('leaves an ordinary policy group exactly as it was', () => {
		expect(rowActionFor({ enforced: true, policyGroupIds: ['g1'] }, split)).toEqual({
			kind: 'remove',
			group: POLICY
		});
	});

	it('changes nothing for a viewer who may not act', () => {
		/**
		 * The non-admin branch runs before the naming lookup and carries no group
		 * name, so a wider `naming` list cannot reach it. Fails if the branches merge.
		 */
		expect(
			rowActionFor(teamMember, split, {
				mayAct: false,
				teamGroupId: 'g-team',
				mayManagePolicy: false
			})
		).toEqual({
			kind: 'masked-team',
			teamGroupId: 'g-team'
		});
		expect(
			rowActionFor(teamMember, split, { mayAct: false, teamGroupId: null, mayManagePolicy: false })
		).toEqual({ kind: 'masked-elsewhere' });
	});
});

// ---------------------------------------------------------------------------
// A team owner, who may manage membership of one group and nothing else
// ---------------------------------------------------------------------------

describe('rowActionFor — a team owner with the power to act', () => {
	const TEAM = 'g-team';
	const ELSEWHERE = 'g-admins';

	/**
	 * Every viewer field is set explicitly. `mayAct: false` and
	 * `mayManagePolicy: false` mean different things, and a partial viewer could
	 * let a branch pass for the wrong reason.
	 */
	const owner = (teamGroupId: string | null = TEAM) => ({
		mayAct: false,
		teamGroupId,
		mayManagePolicy: true
	});
	const readOnlyOwner = (teamGroupId: string | null = TEAM) => ({
		mayAct: false,
		teamGroupId,
		mayManagePolicy: false
	});
	const admin = { mayAct: true, teamGroupId: null, mayManagePolicy: true };
	/**
	 * An administrator on a team dashboard, as the server reports it: `mayAct`
	 * from the role, `teamGroupId` from the address, and `may_manage_team_policy`
	 * true. Without this viewer, dropping `!mayAct` from the owner-branch
	 * condition would pass every test.
	 */
	const adminOnTeamDashboard = { mayAct: true, teamGroupId: TEAM, mayManagePolicy: true };

	const row = (policyGroupIds: string[], enforced = policyGroupIds.length > 0) => ({
		enforced,
		policyGroupIds
	});

	const anyGroups = { naming: [POLICY, TEAM_GROUP], targets: [POLICY] };

	// -- which button, and why ----------------------------------------------

	it('offers Remove when the person is in the team policy', () => {
		expect(rowActionFor(row([TEAM]), anyGroups, owner())).toEqual({
			kind: 'team-remove',
			maskedElsewhere: false
		});
	});

	it('offers Add when the person is not', () => {
		expect(rowActionFor(row([]), anyGroups, owner())).toEqual({
			kind: 'team-add',
			maskedElsewhere: false
		});
	});

	it('offers Add to someone masked only by an administrator group', () => {
		/**
		 * The person is masked, so keying on `enforced` would offer Remove with
		 * nothing to remove. Membership of the team group decides, and they are not
		 * in it.
		 */
		expect(rowActionFor(row([ELSEWHERE]), anyGroups, owner())).toEqual({
			kind: 'team-add',
			maskedElsewhere: true
		});
	});

	it('offers Add to someone masked by the instance default', () => {
		// Enforced with no group behind it: not in the team policy, so Add. No
		// other group enforces them, so `maskedElsewhere` is false.
		expect(rowActionFor(row([], true), anyGroups, owner())).toEqual({
			kind: 'team-add',
			maskedElsewhere: false
		});
	});

	it('offers Add to someone who is not masked at all', () => {
		expect(rowActionFor(row([], false), anyGroups, owner())).toEqual({
			kind: 'team-add',
			maskedElsewhere: false
		});
	});

	it('never returns readonly — the action is always offered', () => {
		for (const groups of [[], [TEAM], [ELSEWHERE], [TEAM, ELSEWHERE]]) {
			expect(rowActionFor(row(groups), anyGroups, owner()).kind).toMatch(/^team-/);
		}
	});

	// -- what the row adds, and what it must never add -----------------------

	it('says the person stays masked when another group also enforces them', () => {
		expect(rowActionFor(row([TEAM, ELSEWHERE]), anyGroups, owner())).toEqual({
			kind: 'team-remove',
			maskedElsewhere: true
		});
	});

	it('carries a boolean and no identity, in every state', () => {
		/**
		 * `maskedElsewhere` is reduced to a boolean inside `rowActionFor`, so
		 * nothing downstream has a group name or id to print.
		 */
		for (const groups of [[], [TEAM], [ELSEWHERE], [TEAM, ELSEWHERE]]) {
			const action = rowActionFor(row(groups), anyGroups, owner());
			expect(Object.keys(action).sort()).toEqual(['kind', 'maskedElsewhere']);
			expect(JSON.stringify(action)).not.toContain(ELSEWHERE);
			expect(JSON.stringify(action)).not.toContain(TEAM);
		}
	});

	// -- the narrowed payload a non-admin actually receives -------------------

	/**
	 * The payload a team owner receives. `pii_policy_group_ids` is narrowed
	 * server-side to the team's own group, because `GET /groups/id/{id}/info`
	 * returns any group's name to any verified user. The id scan sees nothing
	 * else, so `masked_by_other_policy` is the whole answer.
	 */
	const narrowed = (inTeamPolicy: boolean, maskedByOtherPolicy: boolean) => ({
		enforced: inTeamPolicy || maskedByOtherPolicy,
		policyGroupIds: inTeamPolicy ? [TEAM] : [],
		maskedByOtherPolicy
	});

	it('believes the server when the ids can no longer say', () => {
		// The narrowed list holds no other id, so without the server flag the
		// dialog would promise that leaving the team policy lets them unmask.
		expect(rowActionFor(narrowed(true, true), anyGroups, owner())).toEqual({
			kind: 'team-remove',
			maskedElsewhere: true
		});
	});

	it('offers Add, and says they are already masked, on the narrowed shape', () => {
		expect(rowActionFor(narrowed(false, true), anyGroups, owner())).toEqual({
			kind: 'team-add',
			maskedElsewhere: true
		});
	});

	it('says nothing else masks them when the server says so', () => {
		expect(rowActionFor(narrowed(true, false), anyGroups, owner())).toEqual({
			kind: 'team-remove',
			maskedElsewhere: false
		});
		expect(rowActionFor(narrowed(false, false), anyGroups, owner())).toEqual({
			kind: 'team-add',
			maskedElsewhere: false
		});
	});

	it('still carries no identity once the flag is what answers', () => {
		for (const shape of [narrowed(true, true), narrowed(false, true)]) {
			const action = rowActionFor(shape, anyGroups, owner());
			expect(Object.keys(action).sort()).toEqual(['kind', 'maskedElsewhere']);
			expect(JSON.stringify(action)).not.toContain(TEAM);
			expect(JSON.stringify(action)).not.toContain(ELSEWHERE);
		}
	});

	it('keeps the id scan as a second term, for a fuller list', () => {
		// No supported state sends a full list to this branch, but the OR errs
		// towards "still masked" rather than a false promise.
		expect(
			rowActionFor({ ...row([TEAM, ELSEWHERE]), maskedByOtherPolicy: false }, anyGroups, owner())
		).toEqual({ kind: 'team-remove', maskedElsewhere: true });
	});

	// -- the boundaries of the branch ----------------------------------------

	it('falls back to the read-only branch when the team has no group', () => {
		/**
		 * There is no group to add anyone to. Matches the server, which reports
		 * `may_manage_team_policy: false` for a team whose group was never created.
		 */
		expect(rowActionFor(row([ELSEWHERE]), anyGroups, owner(null))).toEqual({
			kind: 'masked-elsewhere'
		});
	});

	it('leaves a read-only viewer exactly as they were', () => {
		expect(rowActionFor(row([TEAM]), anyGroups, readOnlyOwner())).toEqual({
			kind: 'masked-team',
			teamGroupId: TEAM
		});
		expect(rowActionFor(row([ELSEWHERE]), anyGroups, readOnlyOwner())).toEqual({
			kind: 'masked-elsewhere'
		});
		expect(rowActionFor(row([], false), anyGroups, readOnlyOwner())).toEqual({ kind: 'readonly' });
	});

	it('keeps an administrator out of the owner branch even on a team dashboard', () => {
		/**
		 * An administrator here holds all three viewer fields, so only `mayAct`
		 * separates the branches. The admin action names the team group; the
		 * owner's names nothing.
		 */
		expect(
			rowActionFor(
				row([TEAM]),
				{ naming: [{ id: TEAM, name: 'PII — Acme · t1', isTeamGroup: true }], targets: [] },
				adminOnTeamDashboard
			)
		).toEqual({ kind: 'remove', group: { id: TEAM, name: 'PII — Acme · t1', isTeamGroup: true } });
	});

	it('leaves the administrator branch untouched, naming the team', () => {
		/**
		 * An administrator also holds `mayManagePolicy` but must not take the owner
		 * branch: they may reach every group, and their action names the team group.
		 */
		expect(
			rowActionFor(row([TEAM_GROUP.id]), { naming: [TEAM_GROUP], targets: [] }, admin)
		).toEqual({ kind: 'remove', group: TEAM_GROUP });
	});
});
