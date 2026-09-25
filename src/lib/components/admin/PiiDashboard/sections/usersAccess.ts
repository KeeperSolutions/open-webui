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
	/**
	 * Whether this person stays masked without the addressed team's policy group.
	 * Computed server-side over every group and the instance-wide default.
	 *
	 * For a non-admin this is the only source of that answer. Their
	 * `pii_policy_group_ids` is narrowed to the team's own group, because any
	 * other group id reveals that group's name via `GET /groups/id/{id}/info`.
	 */
	masked_by_other_policy?: boolean;
};

/**
 * A group that carries `chat.pii_masking_enforced`.
 *
 * `name` is `null` when a row references a group id that the group list does
 * not contain (see `namedGroup`), so the UI never shows the raw id as a name.
 */
export type PolicyGroup = { id: string; name: string | null; isTeamGroup: boolean };

/**
 * One group's permission tree, as stored. Arbitrarily nested; a truthy leaf
 * means the group grants that permission (see `granted`).
 */
export type PermissionTree = { [key: string]: unknown };

/** The shape of a group as `GET /groups/` returns it, narrowed to what is read. */
export type GroupRecord = {
	id: string;
	name?: string;
	permissions?: PermissionTree | null;
	/**
	 * True for a team's own policy group, per `GET /groups/`. Used only to decide
	 * whether the group may be an enforce destination. Optional so an older
	 * payload reads as `false` instead of hiding every destination.
	 */
	is_team_group?: boolean;
};

/** The permission key that carries the policy. */
const MASKING_PATH = ['chat', 'pii_masking_enforced'] as const;

/**
 * Whether a stored permission leaf is switched on.
 *
 * Truthy, not `=== true`, to match the server: `has_permission_for_groups`
 * (`utils/access_control/__init__.py`) and `group_enforces_pii_masking`
 * (`utils/pii_policy.py`) both end in `bool(...)`, and `GroupForm.permissions`
 * does not validate leaves, so `1` or a non-empty string is a real grant.
 * A stricter check would count such a group as granting nothing, and `Enforce`
 * would then hand its capability to the person being enforced.
 */
function granted(value: unknown): boolean {
	return Boolean(value);
}

/** Does this group carry the policy at all. */
export function enforcesMasking(permissions: GroupRecord['permissions']): boolean {
	const chat = (permissions ?? {})[MASKING_PATH[0]];
	return (
		typeof chat === 'object' && chat !== null && granted((chat as PermissionTree)[MASKING_PATH[1]])
	);
}

/** Every permission this group switches on, as dotted paths. */
function grantedPaths(permissions: GroupRecord['permissions'], prefix = ''): string[] {
	const out: string[] = [];
	for (const [key, value] of Object.entries(permissions ?? {})) {
		// Objects are branches, not leaves: recurse instead of counting them, the
		// same way the server walks the dotted key one level at a time.
		if (typeof value === 'object' && value !== null)
			out.push(...grantedPaths(value as PermissionTree, `${prefix}${key}.`));
		else if (granted(value)) out.push(prefix + key);
	}
	return out;
}

/**
 * Whether this group grants masking and nothing else.
 *
 * Group permissions merge with OR, so joining a group hands over everything it
 * switches on. `Enforce` must only stop a person turning masking off; it must
 * not also grant web search, the code interpreter or other data access.
 * Leaves count when truthy (see `granted`); `false` leaves grant nothing.
 */
export function grantsOnlyMasking(permissions: GroupRecord['permissions']): boolean {
	// Named `paths`, not `granted`, so it does not shadow the `granted()` predicate.
	const paths = grantedPaths(permissions);
	return paths.length === 1 && paths[0] === MASKING_PATH.join('.');
}

/**
 * Every group that enforces masking: the list used to name a source.
 *
 * Kept separate from `enforceTargetsOf`. If the destination filter were applied
 * here, a team member's enforcing team group would be missing from this list
 * and the `Remove` dialog would show a raw group id instead of its name.
 *
 * Derived, never stored: "the policy group" is not a configured thing, it is
 * whichever groups happen to carry the key right now. Deriving it means the
 * answer cannot go stale, and means no governance object gets created behind
 * anyone's back.
 */
export function policyGroupsOf(groups: GroupRecord[]): PolicyGroup[] {
	return groups
		.filter((g) => enforcesMasking(g?.permissions))
		.map((g) => ({
			id: g.id,
			name: g.name || null,
			isTeamGroup: g?.is_team_group === true
		}));
}

/**
 * The groups an admin can enforce through: destinations, not names.
 *
 * Excludes a team's own group, because its membership follows the team and
 * adding an unrelated person would change what the team's policy covers. Also
 * excludes any group that grants more than masking (see `grantsOnlyMasking`).
 * Neither exclusion applies to `policyGroupsOf`, which still names such groups
 * as the source of a person's masking.
 */
export function enforceTargetsOf(groups: GroupRecord[]): PolicyGroup[] {
	const byId = new Map(groups.map((g) => [g.id, g]));
	return policyGroupsOf(groups).filter(
		(g) => !g.isTeamGroup && grantsOnlyMasking(byId.get(g.id)?.permissions)
	);
}

/**
 * The group behind one id, or an explicitly unnamed group.
 *
 * An unknown id happens when the directory references a group the group list
 * has not caught up with. That case logs a console warning and returns
 * `name: null`, so the component shows a sentence instead of a raw id.
 */
function namedGroup(id: string, byId: Map<string, PolicyGroup>): PolicyGroup {
	const known = byId.get(id);
	if (known) return known;

	console.warn(`[PiiDashboard] a user is enforced by group ${id}, which is not in the group list`);
	return { id, name: null, isTeamGroup: false };
}

/**
 * How many enforcing groups were excluded for belonging to a team.
 *
 * Lets the empty state tell "nothing enforces masking" apart from "only team
 * groups enforce it", which need different advice to the admin.
 */
export function teamOnlyPolicyGroupCount(groups: GroupRecord[]): number {
	return groups.filter((g) => enforcesMasking(g?.permissions) && g?.is_team_group === true).length;
}

/**
 * Enforcing groups kept out of the destination list for granting other things.
 *
 * Lets the empty state explain why a group that carries the policy is visible
 * in Groups but not offered as a destination.
 */
export function broadPolicyGroupCount(groups: GroupRecord[]): number {
	return groups.filter(
		(g) =>
			enforcesMasking(g?.permissions) &&
			g?.is_team_group !== true &&
			!grantsOnlyMasking(g?.permissions)
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
	/** Masked by something other than the addressed team's policy. See `AccessUser`. */
	maskedByOtherPolicy: boolean;
	masking: MaskingState;
	cost: number;
	grantedCount: number;
	allModels: boolean;
};

/**
 * Whether a viewer may act on a row, from their role alone.
 *
 * Takes only the role so the decision cannot depend on the address the viewer
 * arrived at: an address selects a scope, not a permission. Display only; the
 * membership routes are admin-only server-side.
 */
export function mayActFor(role: string | undefined | null): boolean {
	return role === 'admin';
}

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
export type RowAction =
	| { kind: 'enforce'; targets: PolicyGroup[] }
	| { kind: 'remove'; group: PolicyGroup }
	| { kind: 'none'; via: PolicyGroup[] }
	// A viewer who may not act. Carries no data (see `rowActionFor`).
	| { kind: 'readonly' }
	/**
	 * Masked by the viewer's own team's policy. Carries only the group id the
	 * viewer already addressed, so no group outside their reach can be shown.
	 */
	| { kind: 'masked-team'; teamGroupId: string }
	| { kind: 'masked-elsewhere' }
	/**
	 * The two actions a team owner has.
	 *
	 * Named for membership of the team's policy group, not for masking: masking
	 * can also come from other groups, so "unmask" would sometimes be false while
	 * "remove from team policy" is always true. For the same reason both are
	 * always offered.
	 *
	 * `maskedElsewhere` is the only data carried: no group id, name or count, so
	 * nothing about groups outside the team can leak.
	 */
	| { kind: 'team-add'; maskedElsewhere: boolean }
	| { kind: 'team-remove'; maskedElsewhere: boolean };

/**
 * Everything about the person looking at the row, in one place.
 *
 * An object rather than positional arguments so `mayAct` and `mayManagePolicy`,
 * two permission booleans with different meanings, cannot be swapped silently.
 */
export type Viewer = {
	/** The administrator flag. Governs the admin branch and the Manage link. */
	mayAct: boolean;
	/** The addressed team's own policy group, or `null`. */
	teamGroupId: string | null;
	/**
	 * Whether this viewer may change who is in that group. Reported by the server
	 * (`may_manage_team_policy`), never derived from the address here.
	 */
	mayManagePolicy: boolean;
};

/** An administrator on the instance-wide view: the default viewer. */
const INSTANCE_ADMIN: Viewer = { mayAct: true, teamGroupId: null, mayManagePolicy: false };

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
 * `mayAct` comes from the viewer's role, never from the address they arrived at.
 * It governs what is displayed and is not a security boundary: the membership
 * routes are admin-only server-side.
 */
export function rowActionFor(
	row: Pick<UserRow, 'enforced' | 'policyGroupIds'> & Partial<Pick<UserRow, 'maskedByOtherPolicy'>>,
	/**
	 * `naming` is every enforcing group; `targets` is the subset a person may be
	 * sent to. Named fields so a caller cannot pass the destination list where
	 * the naming list belongs.
	 */
	groups: { naming: PolicyGroup[]; targets: PolicyGroup[] },
	viewer: Viewer = INSTANCE_ADMIN
): RowAction {
	const { mayAct, teamGroupId, mayManagePolicy } = viewer;

	// Team owner: cannot reach the admin screen but may manage this one group.
	// Checked before the read-only branch; administrators take their own branch
	// below. Requires `teamGroupId`: a team without a policy group has nothing
	// to add anyone to, and the server reports `may_manage_team_policy: false`.
	if (!mayAct && mayManagePolicy && teamGroupId) {
		// Membership of the team group picks the button, not `row.enforced`.
		// Someone masked only through another group is offered Add, because
		// Remove would have nothing to remove.
		const inTeamPolicy = row.policyGroupIds.includes(teamGroupId);
		// Masked by something other than this team's policy. The server field is
		// the complete answer: a non-admin's `policyGroupIds` holds only the team
		// group, and the instance-wide default belongs to no group. The id scan
		// covers an admin's full list; if the server field is missing, the OR
		// errs towards "still masked" rather than implying masking can be turned off.
		const maskedElsewhere =
			row.maskedByOtherPolicy === true || row.policyGroupIds.some((id) => id !== teamGroupId);

		return inTeamPolicy
			? { kind: 'team-remove', maskedElsewhere }
			: { kind: 'team-add', maskedElsewhere };
	}

	if (!mayAct) {
		// Not enforced: renders an em dash. Not `{ kind: 'none', via: [] }`, which
		// the component renders as "Enforced instance-wide".
		if (!row.enforced) return { kind: 'readonly' };

		// Enforced with no group behind it is the instance default, which names no group.
		if (row.policyGroupIds.length === 0) return { kind: 'none', via: [] };

		// Masked by this team's own policy, which the viewer owns. Checked before
		// the outside case, so someone masked by both is shown as team policy only
		// and the existence of a source outside the viewer's reach is not disclosed.
		if (teamGroupId && row.policyGroupIds.includes(teamGroupId)) {
			return { kind: 'masked-team', teamGroupId };
		}

		// Enforced through a group the viewer does not administer. The value has
		// no fields (no group name, id or count), so no template can leak them.
		return { kind: 'masked-elsewhere' };
	}

	if (!row.enforced) return { kind: 'enforce', targets: groups.targets };

	// Named from `naming`, which includes team groups: an admin may take someone
	// out of their team's policy, and the dialog must name that group correctly.
	const byId = new Map(groups.naming.map((g) => [g.id, g]));
	// A group missing from the list is still counted, so a two-source user never
	// gets a `Remove` button that would not actually unlock anything.
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
			maskedByOtherPolicy: u.masked_by_other_policy === true,
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
