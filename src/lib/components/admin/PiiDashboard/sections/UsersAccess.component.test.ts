// @vitest-environment jsdom
/**
 * What the cell actually RENDERS for a viewer who may not act.
 *
 * ⚠️ `rowActionFor`'s unit tests prove the value; only this file proves the
 * markup. They are different failures: returning `{ kind: 'readonly' }` correctly
 * and then rendering it through a branch that says "Enforced instance-wide" would
 * pass every test in `usersAccess.test.ts`.
 *
 * The Manage button has no other coverage at all — it lives entirely in markup.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, screen, fireEvent } from '@testing-library/svelte';
import { readable } from 'svelte/store';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/apis/groups', () => ({ addUserToGroup: vi.fn(), removeUserFromGroup: vi.fn() }));
vi.mock('svelte-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { addUserToGroup, removeUserFromGroup } from '$lib/apis/groups';
import { toast } from 'svelte-sonner';

import UsersAccess from './UsersAccess.svelte';
import type { AccessUser } from './usersAccess';

// Interpolates, because a stub that returns the raw key would hide exactly the
// leak these tests look for: `Enforced via {{groups}}` renders the NAMES.
const i18n = readable({
	t: (k: string, vars?: Record<string, unknown>) =>
		vars ? k.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars[name] ?? '')) : k
});

const account = (over: Partial<AccessUser> = {}): AccessUser => ({
	id: 'u1',
	name: 'Ana',
	email: 'ana@x.com',
	role: 'user',
	pii_masking_enforced: false,
	pii_policy_group_ids: [],
	...over
});

const mount = (props: Record<string, unknown>) =>
	render(UsersAccess, {
		props: {
			users: [account()],
			metricRows: [],
			loading: false,
			failed: false,
			onRetry: () => {},
			policyGroups: [{ id: 'g1', name: 'Global PII Policy', isTeamGroup: false }],
			enforceTargets: [{ id: 'g1', name: 'Global PII Policy', isTeamGroup: false }],
			...props
		},
		context: new Map([['i18n', i18n]])
	});

describe('UsersAccess — a viewer who may not act', () => {
	beforeEach(() => vi.clearAllMocks());

	it('renders no Manage button', () => {
		mount({ mayAct: false });
		expect(screen.queryByText('Manage')).toBeNull();
	});

	it('renders the Manage button for a viewer who may act', () => {
		// The mirror case: without it, the test above passes on a broken selector.
		mount({ mayAct: true });
		expect(screen.queryByText('Manage')).not.toBeNull();
	});

	it('draws no Account column at all', () => {
		// ⚠️ The header, not only the button. `Manage` is the column's ONLY
		// content, so hiding the button alone left a labelled 65px of empty cells
		// — measured on the team board — naming a destination the reader cannot
		// reach, in the one view built to show them less.
		const { container } = mount({ mayAct: false });
		const headers = [...container.querySelectorAll('thead th')].map((th) => th.textContent?.trim());
		expect(headers).not.toContain('Account');
	});

	it('draws the Account column for a viewer who may act', () => {
		const { container } = mount({ mayAct: true });
		const headers = [...container.querySelectorAll('thead th')].map((th) => th.textContent?.trim());
		expect(headers).toContain('Account');
	});

	it('keeps every row as wide as the header', () => {
		// A conditional header and an unconditional cell is the other half of the
		// same bug, and it renders as a table that looks fine until the last
		// column silently shifts under the wrong label.
		for (const mayAct of [false, true]) {
			const { container } = mount({ mayAct });
			const headerCount = container.querySelectorAll('thead th').length;
			for (const row of container.querySelectorAll('tbody tr')) {
				expect(row.querySelectorAll('td').length, `mayAct=${mayAct}`).toBe(headerCount);
			}
		}
	});

	it('keeps the unattributed footer as wide as the header', () => {
		// The footer is a hand-written row of <td>s, so it does not follow the
		// header on its own — it is the cell most likely to be left behind.
		for (const mayAct of [false, true]) {
			const { container } = mount({
				mayAct,
				metricRows: [{ user: 'nobody@x.com', cost: 4.2 }]
			});
			const headerCount = container.querySelectorAll('thead th').length;
			const foot = container.querySelector('tfoot tr');
			expect(foot, `no footer rendered for mayAct=${mayAct}`).not.toBeNull();
			expect(foot!.querySelectorAll('td').length, `mayAct=${mayAct}`).toBe(headerCount);
		}
	});

	it('never lets a header or the role break mid-word', () => {
		// ⚠️ Structural, because jsdom does no layout: the defect is that
		// `html { word-break: break-word }` (`src/app.css:40`, app-wide) turns a
		// squeezed column into "Rol/e", "Cos/t" and "Admi/n". Measured in a real
		// browser on the admin board with somebody in two policy groups — which is
		// what makes the Policy group column wide enough to squeeze the rest.
		const { container } = mount({ mayAct: true });
		for (const th of container.querySelectorAll('thead th')) {
			expect(th.className, `header "${th.textContent?.trim()}"`).toContain('whitespace-nowrap');
		}
		const roleCell = container.querySelector('tbody tr td:nth-child(2)');
		expect(roleCell?.className).toContain('whitespace-nowrap');
	});

	it('does not claim an unmasked person is enforced instance-wide', () => {
		mount({ mayAct: false });
		expect(screen.queryByText('Enforced instance-wide')).toBeNull();
		expect(screen.queryByText('Enforce')).toBeNull();
	});

	it('says the source is outside the team, and names no group', () => {
		mount({
			mayAct: false,
			users: [account({ pii_masking_enforced: true, pii_policy_group_ids: ['g1'] })]
		});
		expect(screen.queryByText('Masked · source outside the team')).not.toBeNull();
		expect(document.body.textContent).not.toContain('Global PII Policy');
		expect(document.body.textContent).not.toContain('g1');
		expect(screen.queryByText('Remove')).toBeNull();
	});

	it('does not leak a group id when the group list cannot name it', () => {
		mount({
			mayAct: false,
			users: [account({ pii_masking_enforced: true, pii_policy_group_ids: ['ghost'] })],
			policyGroups: []
		});
		expect(document.body.textContent).not.toContain('ghost');
	});

	it('still names the instance-wide default, which names no group', () => {
		mount({
			mayAct: false,
			users: [account({ pii_masking_enforced: true, pii_policy_group_ids: [] })]
		});
		expect(screen.queryByText('Enforced instance-wide')).not.toBeNull();
	});

	it('names the group for a viewer who may act', () => {
		// Proves the hiding above is `mayAct`, not a template that never renders it.
		mount({
			mayAct: true,
			users: [account({ pii_masking_enforced: true, pii_policy_group_ids: ['g1', 'g2'] })],
			policyGroups: [
				{ id: 'g1', name: 'Global PII Policy', isTeamGroup: false },
				{ id: 'g2', name: 'Second Policy', isTeamGroup: false }
			]
		});
		expect(document.body.textContent).toContain('Global PII Policy');
	});
});

describe('the empty destination list explains itself', () => {
	const OLD =
		'No group enforces PII masking yet. Turn it on for a group in Admin → Users → Groups → Permissions first.';
	const NEW =
		"Only team policy groups enforce PII masking, and a team's group cannot be used here. Create a group in Admin → Users → Groups, then turn on PII masking in its Permissions.";

	const BROAD =
		'The groups that enforce PII masking also grant other permissions, so they are not offered here — joining one would hand over everything else it grants. Create a group whose only permission is PII masking, in Admin → Users → Groups.';

	const mountEmpty = (teamOnlyPolicyGroups: number, broadPolicyGroups = 0) =>
		render(UsersAccess, {
			props: {
				users: [account()],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				policyGroups: [],
				enforceTargets: [],
				mayAct: true,
				teamOnlyPolicyGroups,
				broadPolicyGroups
			},
			context: new Map([['i18n', i18n]])
		});

	it('says nothing enforces masking when nothing does', () => {
		mountEmpty(0);
		expect(screen.getByTitle(OLD)).toBeTruthy();
	});

	it('says the enforcing groups grant too much when they do', () => {
		// ⚠️ Without this the screen simply drops a group the admin can see in
		// Groups, carrying the policy, with no explanation — which reads as the
		// feature being broken rather than as a deliberate exclusion.
		mountEmpty(0, 1);
		expect(screen.getByTitle(BROAD)).toBeTruthy();
	});

	it('names the broad-group cause first when both apply', () => {
		// Both are true at once on any instance that has a team AND a wide group.
		// The broad one wins because it is the one an admin can act on.
		mountEmpty(1, 1);
		expect(screen.getByTitle(BROAD)).toBeTruthy();
		expect(screen.queryByTitle(NEW)).toBeNull();
	});

	it('keeps the team-group sentence when only that cause applies', () => {
		// The mirror of the case above: the new branch must not swallow the old one.
		mountEmpty(1, 0);
		expect(screen.getByTitle(NEW)).toBeTruthy();
		expect(screen.queryByTitle(BROAD)).toBeNull();
	});

	it('says the enforcing groups are team groups when they are', () => {
		/**
		 * ⚠️ The old sentence is a lie here: groups DO enforce masking, the admin
		 * DID turn it on, and the filter is what hides them. Following it sends
		 * them to switch on something already switched on.
		 */
		mountEmpty(2);
		expect(screen.getByTitle(NEW)).toBeTruthy();
		expect(screen.queryByTitle(OLD)).toBeNull();
	});
});

describe('what the team-policy row actually renders', () => {
	const TEAM = 'g-team';
	const OTHER = 'g-other';

	const mountMasked = (policyGroupIds: string[], teamGroupId: string | null) =>
		render(UsersAccess, {
			props: {
				users: [account({ pii_masking_enforced: true, pii_policy_group_ids: policyGroupIds })],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				policyGroups: [
					{ id: TEAM, name: 'PII — Acme · abcdef01', isTeamGroup: true },
					{ id: OTHER, name: 'Global PII Policy', isTeamGroup: false }
				],
				enforceTargets: [{ id: OTHER, name: 'Global PII Policy', isTeamGroup: false }],
				mayAct: false,
				teamGroupId
			},
			context: new Map([['i18n', i18n]])
		});

	it('says team policy when the team group is the source', () => {
		mountMasked([TEAM], TEAM);
		expect(screen.getByText('Masked · team policy')).toBeTruthy();
		expect(screen.queryByText('Masked · source outside the team')).toBeNull();
	});

	it('says outside the team when it is not', () => {
		mountMasked([OTHER], TEAM);
		expect(screen.getByText('Masked · source outside the team')).toBeTruthy();
	});

	it('names no group and prints no id, in either case', () => {
		/**
		 * ⚠️ The markup half of decision 5. `rowActionFor` carries only the team id,
		 * but only this test proves the template does not reach past it into
		 * `policyGroups`, which is right there in scope and DOES hold the names.
		 */
		const { container } = mountMasked([TEAM, OTHER], TEAM);
		const html = container.innerHTML;
		expect(html).not.toContain('Global PII Policy');
		expect(html).not.toContain('PII — Acme · abcdef01');
		expect(html).not.toContain(OTHER);
	});

	it('falls back to outside-the-team when the team has no group yet', () => {
		mountMasked([OTHER], null);
		expect(screen.getByText('Masked · source outside the team')).toBeTruthy();
	});
});

describe('what an admin sees for somebody else’s team member', () => {
	const TEAM_ID = 'g-team';
	const TEAM_NAME = 'PII — Acme · abcdef01';
	const GLOBAL = { id: 'g1', name: 'Global PII Policy', isTeamGroup: false };
	const TEAM = { id: TEAM_ID, name: TEAM_NAME, isTeamGroup: true };

	const mountAdmin = (policyGroupIds: string[]) =>
		render(UsersAccess, {
			props: {
				users: [account({ pii_masking_enforced: true, pii_policy_group_ids: policyGroupIds })],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				// The split the loader produces: nameable, not targetable.
				policyGroups: [GLOBAL, TEAM],
				enforceTargets: [GLOBAL],
				mayAct: true,
				teamGroupId: null
			},
			context: new Map([['i18n', i18n]])
		});

	it('names the team group next to the button, never its id', () => {
		const { container } = mountAdmin([TEAM_ID]);
		expect(screen.getByText('Remove')).toBeTruthy();
		expect(container.innerHTML).toContain(TEAM_NAME);
		expect(container.innerHTML).not.toContain(TEAM_ID);
	});

	it('says nothing extra for an ordinary policy group', () => {
		// The wording is for the surprising case only; the common one is unchanged.
		const { container } = mountAdmin(['g1']);
		expect(screen.getByText('Remove')).toBeTruthy();
		expect(container.innerHTML).not.toContain(TEAM_NAME);
	});

	it('the confirmation says the group belongs to a team, and names it', async () => {
		/**
		 * ⚠️ The markup half, and the half that was actually leaking. The old
		 * dialog line was `pending.targets[0]?.name ?? pending.groupId` — with the
		 * fallback supplying `name: id`, it printed a raw UUID at the exact moment
		 * an admin was deciding whether to act.
		 */
		mountAdmin([TEAM_ID]);
		screen.getByText('Remove').click();
		await new Promise((r) => setTimeout(r, 0));

		const text = document.body.textContent ?? '';
		expect(text).toContain('which belongs to a team');
		expect(text).toContain(TEAM_NAME);
		expect(text).not.toContain(TEAM_ID);
	});
});

describe('what a team owner sees, and what must never be in the page', () => {
	const TEAM = 'g-team';
	const TEAM_NAME = 'PII — Acme · abcdef01';
	const ELSEWHERE = 'g-admins';
	const ELSEWHERE_NAME = 'Legal hold';

	/**
	 * ⚠️ Every viewer field spelled out, none left to a default.
	 *
	 * `mayAct: false` and `mayManagePolicy: true` are the whole difference between
	 * this viewer and the read-only one, and a corpus that omitted either would
	 * let a branch pass for the wrong reason — the failure that let M5 survive in
	 * G-B7 and C5-M3 survive in G-C5.
	 */
	const mountOwner = (policyGroupIds: string[]) =>
		render(UsersAccess, {
			props: {
				users: [account({ pii_masking_enforced: true, pii_policy_group_ids: policyGroupIds })],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				// The naming list holds BOTH names, so a template that reached past
				// the action into it would have something to print. That is the point.
				policyGroups: [
					{ id: TEAM, name: TEAM_NAME, isTeamGroup: true },
					{ id: ELSEWHERE, name: ELSEWHERE_NAME, isTeamGroup: false }
				],
				enforceTargets: [{ id: ELSEWHERE, name: ELSEWHERE_NAME, isTeamGroup: false }],
				mayAct: false,
				mayManagePolicy: true,
				teamGroupId: TEAM
			},
			context: new Map([['i18n', i18n]])
		});

	it('offers Remove when the person is in the team policy', () => {
		mountOwner([TEAM]);
		expect(screen.getByText('Remove from team policy')).toBeTruthy();
		expect(screen.queryByText('Add to team policy')).toBeNull();
	});

	it('⚠️ offers Add to someone masked only by an administrator group', () => {
		// Decision 9 in the markup. They ARE masked; they are not in the team
		// policy, so what the owner is offered is Add.
		mountOwner([ELSEWHERE]);
		expect(screen.getByText('Add to team policy')).toBeTruthy();
		expect(screen.getByText('Masked · source outside the team')).toBeTruthy();
	});

	it('says the removal will not unlock, without naming what does', () => {
		mountOwner([TEAM, ELSEWHERE]);
		expect(screen.getByText('Remove from team policy')).toBeTruthy();
		expect(screen.getByText('Will stay masked · source outside the team')).toBeTruthy();
	});

	it('⚠️ names no group and prints no id, in every state — over outerHTML', () => {
		/**
		 * ⚠️ `outerHTML`, not the cell's text. Decision 5 asks that the data not be
		 * IN the page, not merely that it be invisible — a title attribute, an aria
		 * label or a value bound to a hidden control leaks just as well. That
		 * difference is what caught the raw UUID in the G-B9 dialog, which the
		 * table's text had shown nothing of.
		 */
		for (const groups of [[], [TEAM], [ELSEWHERE], [TEAM, ELSEWHERE]]) {
			const { container } = mountOwner(groups);
			const html = container.innerHTML;
			expect(html).not.toContain(TEAM_NAME);
			expect(html).not.toContain(ELSEWHERE_NAME);
			expect(html).not.toContain(TEAM);
			expect(html).not.toContain(ELSEWHERE);
		}
	});

	it('⚠️ renders no Manage button', () => {
		// M-1 from the spec, and the only reason there are two flags: `Manage`
		// hangs off `mayAct`, which links to the admin user screen. Widening that
		// one flag instead of adding a second would hand the owner a page the
		// server refuses them.
		mountOwner([TEAM]);
		expect(screen.queryByText('Manage')).toBeNull();
	});
});

describe("the owner's confirmation dialog", () => {
	const TEAM = 'g-team';
	const TEAM_NAME = 'PII — Acme · abcdef01';
	const ELSEWHERE = 'g-admins';

	const open = async (policyGroupIds: string[], label: string) => {
		const view = render(UsersAccess, {
			props: {
				users: [account({ pii_masking_enforced: true, pii_policy_group_ids: policyGroupIds })],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				policyGroups: [
					{ id: TEAM, name: TEAM_NAME, isTeamGroup: true },
					{ id: ELSEWHERE, name: 'Legal hold', isTeamGroup: false }
				],
				enforceTargets: [],
				mayAct: false,
				mayManagePolicy: true,
				teamGroupId: TEAM
			},
			context: new Map([['i18n', i18n]])
		});
		screen.getByText(label).click();
		await new Promise((r) => setTimeout(r, 0));
		return view;
	};

	it('⚠️ has no Group line at all', async () => {
		/**
		 * O-C5, and the omission is a CONSEQUENCE OF DECISION 5 rather than an
		 * oversight. The admin dialog names its group; this one must not, and the
		 * reason is written beside the branch in the component so nobody adds
		 * `Group:` back "for consistency" and reopens what G-B9 closed.
		 */
		await open([TEAM], 'Remove from team policy');
		expect(document.body.textContent).not.toContain('Group:');
		expect(document.body.innerHTML).not.toContain(TEAM_NAME);
		expect(document.body.innerHTML).not.toContain(TEAM);
	});

	it('names the team policy, never a group', async () => {
		await open([TEAM], 'Remove from team policy');
		expect(document.body.textContent).toContain("will be removed from your team's policy");
	});

	it('says they stay masked when another group also enforces them', async () => {
		await open([TEAM, ELSEWHERE], 'Remove from team policy');
		expect(document.body.textContent).toContain('they will stay masked');
		expect(document.body.innerHTML).not.toContain(ELSEWHERE);
	});

	it('⚠️ keeps the confirm button really disabled until a reason is given', async () => {
		/**
		 * The real `disabled` attribute, not a visual state: decision 8 makes the
		 * reason mandatory unconditionally, the model refuses a removal without
		 * one, and a keyboard reaching a merely dimmed button would be a dead
		 * keypress that assistive technology announced as live.
		 */
		await open([TEAM], 'Remove from team policy');
		const confirm = [...document.querySelectorAll('button')].find(
			(b) => b.textContent?.trim() === 'Remove from team policy' && b.closest('[role="dialog"]')
		);
		expect(confirm).toBeTruthy();
		expect(confirm?.hasAttribute('disabled')).toBe(true);
	});

	it('asks for no reason when adding', async () => {
		await open([], 'Add to team policy');
		expect(document.body.textContent).toContain("will be added to your team's policy");
		const confirm = [...document.querySelectorAll('button')].find(
			(b) => b.textContent?.trim() === 'Add to team policy' && b.closest('[role="dialog"]')
		);
		expect(confirm?.hasAttribute('disabled')).toBe(false);
	});

	it('⚠️ promises no change of state to somebody another group already masks', async () => {
		/**
		 * The case the whole membership model exists for: they are masked, they are
		 * not in the team's policy, so what they are offered is Add. Saying they
		 * "will no longer be able to turn PII masking off" describes something that
		 * has ALREADY happened, through a group the owner does not control and
		 * cannot see — so the sentence promises a change this action does not make.
		 */
		await open([ELSEWHERE], 'Add to team policy');
		expect(document.body.textContent).toContain("will be added to your team's policy");
		expect(document.body.textContent).not.toContain('will no longer be able to turn PII masking');
	});
});

describe("the owner's success message", () => {
	const TEAM = 'g-team';
	const ELSEWHERE = 'g-admins';

	beforeEach(() => vi.clearAllMocks());

	const openFor = async (policyGroupIds: string[], label: string) => {
		render(UsersAccess, {
			props: {
				users: [account({ pii_masking_enforced: true, pii_policy_group_ids: policyGroupIds })],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				policyGroups: [
					{ id: TEAM, name: 'PII — Acme · abcdef01', isTeamGroup: true },
					{ id: ELSEWHERE, name: 'Legal hold', isTeamGroup: false }
				],
				enforceTargets: [],
				mayAct: false,
				mayManagePolicy: true,
				teamGroupId: TEAM
			},
			context: new Map([['i18n', i18n]])
		});
		screen.getByText(label).click();
		await new Promise((r) => setTimeout(r, 0));
	};

	const confirm = async (label: string) => {
		const button = [...document.querySelectorAll('button')].find(
			(b) => b.textContent?.trim() === label && b.closest('[role="dialog"]')
		);
		button!.click();
		await new Promise((r) => setTimeout(r, 0));
	};

	it('⚠️ never claims masking stopped when another group still enforces it', async () => {
		/**
		 * The defect this pins is a SELF-CONTRADICTION inside one interaction: the
		 * dialog says "they will stay masked", the owner confirms, and the toast
		 * announces that masking is no longer enforced.
		 *
		 * The admin keeps the effect wording, and correctly — they are only offered
		 * `Remove` when exactly one group carries the policy, so for them the
		 * effect really is what changed.
		 */
		vi.mocked(removeUserFromGroup).mockResolvedValue({} as never);
		await openFor([TEAM, ELSEWHERE], 'Remove from team policy');
		await fireEvent.input(document.querySelector('#pii-policy-reason')!, {
			target: { value: 'left the project' }
		});
		await confirm('Remove from team policy');

		expect(toast.success).toHaveBeenCalledWith("Ana is no longer in your team's policy.");
		expect(toast.success).not.toHaveBeenCalledWith(
			'PII masking is no longer enforced for this user.'
		);
	});

	it('reports the addition as membership too', async () => {
		vi.mocked(addUserToGroup).mockResolvedValue({} as never);
		await openFor([ELSEWHERE], 'Add to team policy');
		await confirm('Add to team policy');

		expect(toast.success).toHaveBeenCalledWith("Ana is now in your team's policy.");
	});
});

describe("the owner's pending action carries no group", () => {
	it('⚠️ opens with an empty targets array, and that is the second lock', () => {
		/**
		 * Found by a mutation that SURVIVED: filling `targets` with the team's
		 * group broke nothing, because the template's `teamPolicy` check hides the
		 * `Group:` line anyway.
		 *
		 * It matters for the case where that check is the thing that fails. With
		 * `targets: []` the dialog would print "Unknown group"; with the group in
		 * there it prints the NAME — which is the leak G-B9 closed, arriving by a
		 * different door. One lock is a template condition somebody can delete; the
		 * other is the absence of the data.
		 *
		 * Structural because the property is about what is HANDED to the dialog,
		 * and a rendered page cannot show the difference while the template check
		 * still holds. The same kind of test as the route-ordering one in G-C3.
		 */
		const source = readFileSync(
			resolve(process.cwd(), 'src/lib/components/admin/PiiDashboard/sections/UsersAccess.svelte'),
			'utf-8'
		);
		const opener = source.slice(
			source.indexOf('const openTeamAction'),
			source.indexOf('const submitAction')
		);
		const assignments = opener.match(/targets:.*/g) ?? [];
		expect(assignments).toEqual(['targets: [],']);
	});
});
