import type { MetricRow } from '$lib/apis/langfuse';
import { getStoredPiiMasking, type StoredPiiMasking } from '$lib/utils/pii';
import { grantedModelIds, type ModelRecord } from '../modelAccess';
import { normalizeUserKey } from './costAnalytics';

export type UserStatus = 'pending' | 'inactive' | 'active';

/** The subset of an OWUI directory user this section renders. */
export type AccessUser = {
	id: string;
	name: string;
	email: string;
	role: string;
	group_ids?: string[];
	settings?: { ui?: Record<string, unknown> } | null;
	/** Team policy, resolved server-side. Report-only; nothing writes it here. */
	pii_masking_enforced?: boolean;
	/**
	 * Which of this user's groups carry the policy — server-side, same source.
	 *
	 * ⚠️ Empty while `pii_masking_enforced` is true is meaningful, not missing
	 * data: the instance-wide default is the source, and no membership change
	 * can undo it.
	 */
	pii_policy_group_ids?: string[];
};

/**
 * A group that carries `chat.pii_masking_enforced`.
 *
 * `name` is `null` for a group a row claims to be enforced by that the group
 * list does not contain — see `namedGroup`. It is a state nobody designed, so it
 * is representable rather than papered over with the id.
 */
export type PolicyGroup = { id: string; name: string | null; isTeamGroup: boolean };

/** The shape of a group as `GET /groups/` returns it, narrowed to what is read. */
export type GroupRecord = {
	id: string;
	name?: string;
	permissions?: { chat?: { pii_masking_enforced?: boolean } } | null;
	/**
	 * A team's own policy group, per `GET /groups/`.
	 *
	 * ⚠️ A flag, not a team id: the only question here is "may this be an enforce
	 * destination". Optional so an older payload reads as `false` rather than
	 * throwing — the wrong direction to fail would be hiding every destination.
	 */
	is_team_group?: boolean;
};

/**
 * Every group that enforces masking — the list used to NAME a source.
 *
 * ⚠️ This and `enforceTargetsOf` are two different questions about the same
 * groups, and they were one list until a team group could not be a destination.
 * Conflating them is what produced the defect this split exists to fix: the
 * destination filter was applied to the naming list too, so an admin looking at
 * a team member saw a `Remove` button whose dialog printed a raw UUID, for a
 * group belonging to somebody else's team, with nothing saying so.
 *
 * "May I send someone here?" is a policy question. "What is this called?" is
 * not. Keep them apart.
 *
 * Derived, never stored: "the policy group" is not a configured thing, it is
 * whichever groups happen to carry the key right now. Deriving it means the
 * answer cannot go stale, and means no governance object gets created behind
 * anyone's back.
 */
export function policyGroupsOf(groups: GroupRecord[]): PolicyGroup[] {
	return groups
		.filter((g) => g?.permissions?.chat?.pii_masking_enforced === true)
		.map((g) => ({
			id: g.id,
			name: g.name || null,
			isTeamGroup: g?.is_team_group === true
		}));
}

/**
 * The groups an admin can enforce THROUGH — destinations, not names.
 *
 * A team's group is excluded: it belongs to that team, its membership follows
 * the team, and sending an unrelated person into it would make the team's own
 * policy mean something else.
 */
export function enforceTargetsOf(groups: GroupRecord[]): PolicyGroup[] {
	return policyGroupsOf(groups).filter((g) => !g.isTeamGroup);
}

/**
 * The group behind one id, named — or explicitly unnamed.
 *
 * ⚠️ Replaces `?? { id, name: id }`, which put a raw UUID where a group name
 * goes. That fallback was written for a race — a group the directory knows about
 * that the group list has not caught up with — and the team-group filter quietly
 * turned it into the ordinary path, firing for every team member on every load.
 *
 * Now it fires only in the state it was written for, says so in the console, and
 * hands back `name: null` so the component prints a sentence instead of an id. A
 * fallback nobody can see is a fallback nobody fixes.
 */
function namedGroup(id: string, byId: Map<string, PolicyGroup>): PolicyGroup {
	const known = byId.get(id);
	if (known) return known;

	console.warn(
		`[PiiDashboard] a user is enforced by group ${id}, which is not in the group list`
	);
	return { id, name: null, isTeamGroup: false };
}

/**
 * How many enforcing groups were excluded for belonging to a team.
 *
 * ⚠️ Exists so the empty state can tell its two causes apart. "No destinations"
 * because nothing enforces masking and "no destinations because the only things
 * that enforce it are team groups" need opposite advice, and the count is the
 * only thing that distinguishes them. Without it the screen tells an admin to
 * turn on something they already turned on.
 */
export function teamOnlyPolicyGroupCount(groups: GroupRecord[]): number {
	return groups.filter(
		(g) => g?.permissions?.chat?.pii_masking_enforced === true && g?.is_team_group === true
	).length;
}

/**
 * What the masking column states about one user.
 *
 * ⚠️ Only `off` is a risk. `default` means the user never chose — and with no
 * stored valve the pipeline masks anyway, so those users ARE protected. Anything
 * rendering these must not colour `default` as a warning.
 */
export type MaskingState = 'enforced' | 'default' | 'on' | 'off';

export type UserRow = {
	id: string;
	name: string;
	email: string;
	role: string;
	status: UserStatus;
	enforced: boolean;
	/** Ids of this user's groups that carry the policy. See `AccessUser`. */
	policyGroupIds: string[];
	masking: MaskingState;
	cost: number;
	grantedCount: number;
	allModels: boolean;
};

/**
 * What, if anything, the row may do about this user's policy.
 *
 * `enforce.targets` puts the choice of destination in the data rather than in the
 * component: one target acts straight away, several ask which, none disables the
 * action. The component never picks a destination of its own, and nothing is
 * remembered between calls.
 *
 * `none.via` is the middle case — the one that matters. An empty `via` means the
 * instance-wide default, not "unknown".
 */
/**
 * Whether a viewer may act on a row, from their role alone.
 *
 * ⚠️ A function of the ROLE and nothing else, and that is the point of extracting
 * it: an address selects a scope, not a permission, so the decision must not be
 * able to see the address. Written as `mayActFor(role)` rather than as a line in
 * the component, the team id is not in scope to be consulted even by mistake —
 * the same reasoning that keeps `masked-elsewhere` empty.
 *
 * Display only. The membership routes are admin-only server-side.
 */
export function mayActFor(role: string | undefined | null): boolean {
	return role === 'admin';
}

export type RowAction =
	| { kind: 'enforce'; targets: PolicyGroup[] }
	| { kind: 'remove'; group: PolicyGroup }
	| { kind: 'none'; via: PolicyGroup[] }
	// A viewer who may not act. Carries nothing, deliberately — see `rowActionFor`.
	| { kind: 'readonly' }
	/**
	 * Masked by the viewer's OWN team's policy.
	 *
	 * ⚠️ Carries one id, and it is the id the viewer already addressed. That is
	 * what keeps decision 5 intact: no group outside their reach is named, or even
	 * present, so none can be leaked by an incautious template.
	 */
	| { kind: 'masked-team'; teamGroupId: string }
	| { kind: 'masked-elsewhere' };

/**
 * The action offered on one row.
 *
 * ⚠️ The rule is "never offer what would not do what it says". Because groups
 * merge with "any group wins", removing someone from one enforcing group leaves
 * them enforced if another one also does — so `Remove` is offered ONLY when
 * exactly one group is the source. Every other enforced case offers nothing and
 * names where the policy comes from instead.
 *
 * The mirror case is covered by the same shape: an enforced user never reaches
 * the `enforce` branch, so the action can never produce an audit row for a
 * change that changed nothing.
 *
 * Membership is the only thing this touches — the value of the policy still
 * lives on the group and is edited only in `Permissions.svelte`.
 *
 * `mayAct` is the viewer's ROLE, never the address they arrived at: an address
 * selects a scope, not a permission. It governs what is DISPLAYED and is not a
 * security boundary — the membership routes are admin-only server-side, and this
 * ticket does not touch them.
 */
export function rowActionFor(
	row: Pick<UserRow, 'enforced' | 'policyGroupIds'>,
	/**
	 * ⚠️ Two lists, named, rather than one used for both. `naming` is every
	 * enforcing group; `targets` is the subset a person may be sent to. Passing
	 * an object rather than two positional arrays is deliberate: a caller has to
	 * say which is which, and the bug this replaced was exactly a caller handing
	 * the destination list to the naming code without noticing.
	 */
	groups: { naming: PolicyGroup[]; targets: PolicyGroup[] },
	mayAct: boolean = true,
	teamGroupId: string | null = null
): RowAction {
	if (!mayAct) {
		// Not enforced: an em dash. NOT `{ kind: 'none', via: [] }` — the component
		// renders an empty `via` as "Enforced instance-wide", so reusing it here
		// would tell an unmasked person they are masked instance-wide.
		if (!row.enforced) return { kind: 'readonly' };

		// Enforced with no group behind it IS the instance default, and saying so
		// names no group. Unchanged, and outside level A to revisit.
		if (row.policyGroupIds.length === 0) return { kind: 'none', via: [] };

		// Masked by this team's own policy. Named, because the viewer owns it and
		// "somewhere outside the team" would be false.
		//
		// ⚠️ Checked BEFORE the outside case, so someone masked by both reads as
		// team policy and the other source is not mentioned at all. Saying "and
		// also elsewhere" would disclose that a source outside their reach exists,
		// which is the thing decision 5 withholds.
		if (teamGroupId && row.policyGroupIds.includes(teamGroupId)) {
			return { kind: 'masked-team', teamGroupId };
		}

		// Enforced through a group the viewer does not administer. The row says a
		// source exists and that it is outside the team, and stops there.
		//
		// ⚠️ This value carries NO fields — not the group's name, not its id, not
		// even how many there are. That is the protection, not the markup: an
		// object without the name cannot leak the name, whereas a `via` array plus
		// a careful template is one incautious edit away from printing it.
		return { kind: 'masked-elsewhere' };
	}

	if (!row.enforced) return { kind: 'enforce', targets: groups.targets };

	// ⚠️ Named from `naming`, which includes team groups. An admin MAY take
	// someone out of their team's policy — they are not bound by the seat limit,
	// not exempt from the policy, and a team's group is not untouchable to them.
	// What they may not have is an action that does something other than what it
	// says, which is what a destination-filtered naming list produced.
	const byId = new Map(groups.naming.map((g) => [g.id, g]));
	// Total by construction: a group missing from the list is still counted, so a
	// two-source user does not become a one-source user and gain a `Remove`
	// button that would not actually unlock anything.
	const via = row.policyGroupIds.map((id) => namedGroup(id, byId));

	if (via.length === 1) return { kind: 'remove', group: via[0] };
	return { kind: 'none', via };
}

/**
 * The registered catalogue this section measures access against.
 *
 * `truncated` matters: "granted equals total" cannot be claimed when the total
 * is unknown, so a cut-off catalogue suppresses `allModels` rather than
 * asserting it from a partial count.
 */
export type ModelCatalogue = { models: ModelRecord[]; truncated: boolean };

/**
 * Status of one directory user.
 *
 * A strict hierarchy, not a chain of conditions: `pending` outranks everything
 * because such an account is locked out at `get_verified_user` and can never
 * spend — so its zero is structural, and reads as a different admin action
 * (approve access) than an idle licence (reallocate).
 */
export function statusOf(user: AccessUser, hasUsage: boolean): UserStatus {
	if (user.role === 'pending') return 'pending';
	return hasUsage ? 'active' : 'inactive';
}

/**
 * Cost per Langfuse identity, keyed by `normalizeUserKey`.
 *
 * Keys are Langfuse's, not the directory's: a key with no matching account
 * still appears here. Attribution to accounts happens in `buildRows`.
 */
export function costByUser(rows: MetricRow[]): Map<string, number> {
	const out = new Map<string, number>();
	for (const r of rows) {
		const key = normalizeUserKey(r.user);
		out.set(key, (out.get(key) ?? 0) + r.cost);
	}
	return out;
}

/**
 * Which directory user, if any, owns a given Langfuse identity key.
 *
 * A user is traceable under both their email and their id, so both claim the
 * key. When two users would claim the same key — one's email equal to another's
 * id — the first in directory order wins, so a row is never counted twice.
 */
function claimKeys(users: AccessUser[]): Map<string, number> {
	const owner = new Map<string, number>();
	users.forEach((u, index) => {
		for (const traced of [u.email, u.id]) {
			const key = normalizeUserKey(traced ?? '');
			if (key && !owner.has(key)) owner.set(key, index);
		}
	});
	return owner;
}

/**
 * One row per directory user, with the spend attributed to them in this window.
 *
 * Rows whose identity matches no account are deliberately dropped here and
 * accounted for by `unattributedCost`, so the two together are exhaustive.
 */
export function buildRows(
	users: AccessUser[],
	metricRows: MetricRow[],
	catalogue: ModelCatalogue = { models: [], truncated: false }
): UserRow[] {
	const owner = claimKeys(users);
	const cost = new Array<number>(users.length).fill(0);
	// Usage is "was seen at all", not "cost is non-zero": a refund can net a
	// user's spend to exactly zero without meaning they were idle.
	const seen = new Array<boolean>(users.length).fill(false);

	for (const r of metricRows) {
		const index = owner.get(normalizeUserKey(r.user));
		if (index === undefined) continue;
		cost[index] += r.cost;
		seen[index] = true;
	}

	const total = catalogue.models.length;

	return users.map((u, index) => {
		const grantedCount = grantedModelIds(u, catalogue.models).size;
		return {
			id: u.id,
			name: u.name,
			email: u.email,
			role: u.role,
			status: statusOf(u, seen[index]),
			enforced: u.pii_masking_enforced === true,
			policyGroupIds: u.pii_policy_group_ids ?? [],
			masking: maskingStateOf(
				u.pii_masking_enforced === true,
				getStoredPiiMasking(u.settings?.ui ?? {})
			),
			cost: cost[index],
			grantedCount,
			allModels: !catalogue.truncated && total > 0 && grantedCount === total
		};
	});
}

/**
 * The masking state shown for one user.
 *
 * ⚠️ Policy is checked FIRST and unconditionally. Under an enforced policy the
 * effective value is ON no matter what the user stored, so `off` must be
 * unreachable — otherwise the governance table reports a risk that does not
 * exist, which is exactly the contradiction this column was rebuilt to remove.
 *
 * `unset` maps to `default`, not to `off`: an absent valve means the backend
 * sends no key and the pipeline masks by default.
 */
export function maskingStateOf(enforced: boolean, stored: StoredPiiMasking): MaskingState {
	if (enforced) return 'enforced';
	if (stored === 'unset') return 'default';
	return stored ? 'on' : 'off';
}

/**
 * Sort rank for the masking column: risk first.
 *
 * Ascending puts `off` at the top, which is the only state that needs an admin
 * to look. Mirrors the previous boolean ordering, where `false` sorted first.
 */
export function maskingRank(state: MaskingState): number {
	return { off: 0, default: 1, on: 2, enforced: 3 }[state];
}

/** How many rows one page of the table shows. */
export const ROWS_PER_PAGE = 10;

/**
 * The requested page, brought inside the range the list actually has.
 *
 * Shared by `pageOf` and `pageRange` so the rows on screen and the count that
 * describes them cannot disagree — a summary reading "11 to 20" over the first
 * ten rows is worse than no summary at all.
 */
function clampPage(total: number, page: number, perPage: number): number {
	const lastPage = Math.max(1, Math.ceil(total / perPage));
	return Math.min(Math.max(1, Math.floor(page) || 1), lastPage);
}

/**
 * The page an already-sorted list should show.
 *
 * ⚠️ Paging is applied AFTER sorting, over the whole fetched set — never by
 * asking the server for a page. Three of the five sort keys are not database
 * columns (`status` and `masking` are computed, `cost` comes from Langfuse), so
 * a server-side page would sort only the rows that happen to be on screen. This
 * is presentation, and nothing about the section's arithmetic depends on it:
 * `unattributedCost` still reads every user, so the reconciliation with section
 * 3 holds across the table rather than per page.
 *
 * Clamps rather than trusting the caller. A page number can outlive the list it
 * indexed — sorting changes, and a policy action reloads the section — and an
 * out-of-range page would render an empty table that looks like "no users".
 */
export function pageOf<T>(list: T[], page: number, perPage: number = ROWS_PER_PAGE): T[] {
	if (perPage <= 0) return list;
	const start = (clampPage(list.length, page, perPage) - 1) * perPage;
	return list.slice(start, start + perPage);
}

/**
 * Which rows of the whole list the current page covers, as 1-based positions.
 *
 * Feeds the "Showing 1 to 10 of 57 users" line beside the pager. Split out of
 * the markup because it is the one part of that line that can be wrong: the
 * numbers have to describe the same slice `pageOf` returns, including when the
 * page is out of range, and that is worth a test.
 *
 * `to` is inclusive, so a short last page reports its real end rather than
 * `page * perPage`. An empty list gives `1 to 0`, which no caller renders — the
 * section shows "No data found" long before this — but the function stays total.
 */
export function pageRange(
	total: number,
	page: number,
	perPage: number = ROWS_PER_PAGE
): { from: number; to: number } {
	// Mirrors `pageOf`: a non-positive page size means "no paging", so the range
	// is the whole list.
	if (perPage <= 0) return { from: 1, to: total };
	const from = (clampPage(total, page, perPage) - 1) * perPage + 1;
	return { from, to: Math.min(from + perPage - 1, total) };
}

/**
 * Which i18n key renders a granted-model count.
 *
 * Returns the key, not the translated string, so `$i18n.t()` stays in the
 * component — and so the choice between singular and plural is reachable from a
 * unit test, which the markup around it is not.
 *
 * Two keys rather than i18next plurals: the `en-US` catalogue carries empty
 * values, so `_one`/`_other` resolve back to the base key and a single grant
 * reads "1 models". This applies to every counted string in the app, not just
 * this one.
 *
 * Zero takes the plural, which English agrees with. The cell renders an em dash
 * at zero instead of calling this, so that branch is unreachable from the table
 * — the function stays total anyway rather than leaving a hole for the next
 * caller to find.
 */
export function modelsCountKey(count: number): string {
	return count === 1 ? '1 model' : '{{count}} models';
}

/**
 * Spend Langfuse recorded against an identity no account here claims.
 *
 * Exhaustive with `buildRows`: every row lands in exactly one of the two, so
 * the table column plus this figure reconcile with section 3's total cost.
 */
export function unattributedCost(metricRows: MetricRow[], users: AccessUser[]): number {
	const owner = claimKeys(users);
	let total = 0;
	for (const r of metricRows) {
		if (owner.has(normalizeUserKey(r.user))) continue;
		total += r.cost;
	}
	return total;
}
