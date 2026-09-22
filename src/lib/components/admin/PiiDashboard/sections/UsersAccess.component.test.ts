// @vitest-environment jsdom
/**
 * Rendered output of the users table for each kind of viewer.
 *
 * `usersAccess.test.ts` covers the values `rowActionFor` returns; this file
 * covers the markup that renders them, including the Manage button.
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

// Interpolates, because a stub that returns the raw key would hide group names
// rendered through placeholders such as `Enforced via {{groups}}`.
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
		// The header is hidden too, because `Manage` is the column's only content.
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
		// A conditional header with an unconditional cell would shift the last
		// column under the wrong label.
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
		// header automatically.
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
		// Checks classes because jsdom does no layout. The app-wide
		// `html { word-break: break-word }` in `src/app.css` splits words in a
		// squeezed column unless the cell sets `whitespace-nowrap`.
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
		// Otherwise a policy group visible in Groups is missing here with no reason given.
		mountEmpty(0, 1);
		expect(screen.getByTitle(BROAD)).toBeTruthy();
	});

	it('names the broad-group cause first when both apply', () => {
		// Both apply on any instance with a team and a broad group. The broad
		// cause wins because an admin can act on it.
		mountEmpty(1, 1);
		expect(screen.getByTitle(BROAD)).toBeTruthy();
		expect(screen.queryByTitle(NEW)).toBeNull();
	});

	it('keeps the team-group sentence when only that cause applies', () => {
		// The mirror of the case above: the broad-group branch must not hide this one.
		mountEmpty(1, 0);
		expect(screen.getByTitle(NEW)).toBeTruthy();
		expect(screen.queryByTitle(BROAD)).toBeNull();
	});

	it('says the enforcing groups are team groups when they are', () => {
		// "No group enforces PII masking" would be false here: groups do enforce
		// it, and the team-group filter is what hides them.
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
		// The template must not read names from `policyGroups`, which is in scope
		// and holds them, even though `rowActionFor` carries only the team id.
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
		// The group name appears under the button only for team groups.
		const { container } = mountAdmin(['g1']);
		expect(screen.getByText('Remove')).toBeTruthy();
		expect(container.innerHTML).not.toContain(TEAM_NAME);
	});

	it('the confirmation says the group belongs to a team, and names it', async () => {
		// The dialog must name the team group and never fall back to its raw id.
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
	 * Every viewer field is set explicitly. `mayAct: false` with
	 * `mayManagePolicy: true` is what distinguishes an owner from a read-only
	 * viewer, so relying on a default could let a branch pass for the wrong reason.
	 */
	const mountOwner = (policyGroupIds: string[]) =>
		render(UsersAccess, {
			props: {
				users: [account({ pii_masking_enforced: true, pii_policy_group_ids: policyGroupIds })],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				// The naming list holds both names, so a template that read from it
				// would have something to leak.
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

	it('offers Add to someone masked only by an administrator group', () => {
		// They are masked, but not by the team policy, so the owner is offered Add.
		mountOwner([ELSEWHERE]);
		expect(screen.getByText('Add to team policy')).toBeTruthy();
		expect(screen.getByText('Masked · source outside the team')).toBeTruthy();
	});

	it('says the removal will not unlock, without naming what does', () => {
		mountOwner([TEAM, ELSEWHERE]);
		expect(screen.getByText('Remove from team policy')).toBeTruthy();
		expect(screen.getByText('Will stay masked · source outside the team')).toBeTruthy();
	});

	it('names no group and prints no id, in every state — over outerHTML', () => {
		// Checks the HTML, not the text: a title, aria-label or hidden value would
		// leak a group name or id just as visible text would.
		for (const groups of [[], [TEAM], [ELSEWHERE], [TEAM, ELSEWHERE]]) {
			const { container } = mountOwner(groups);
			const html = container.innerHTML;
			expect(html).not.toContain(TEAM_NAME);
			expect(html).not.toContain(ELSEWHERE_NAME);
			expect(html).not.toContain(TEAM);
			expect(html).not.toContain(ELSEWHERE);
		}
	});

	it('renders no Manage button', () => {
		// `Manage` depends on `mayAct` and links to the admin user screen, which the
		// server refuses an owner. That is why owners get a separate flag.
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

	it('has no Group line at all', async () => {
		// The owner's dialog must not name any group, unlike the admin dialog.
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

	it('keeps the confirm button really disabled until a reason is given', async () => {
		// The real `disabled` attribute, not a visual state: a removal requires a
		// reason, and a merely dimmed button would still be announced as usable.
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

	it('promises no change of state to somebody another group already masks', async () => {
		// Another group already masks them, so the dialog must not promise that
		// adding them will stop them turning masking off.
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

	it('never claims masking stopped when another group still enforces it', async () => {
		// The toast must not contradict the dialog, which said they stay masked.
		// Admins keep the effect wording because they are offered `Remove` only when
		// exactly one group carries the policy.
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
	it('opens with an empty targets array, and that is the second lock', () => {
		/**
		 * Empty `targets` is a second safeguard behind the template's `teamPolicy`
		 * check: if that check were removed, the dialog would print "Unknown group"
		 * instead of the team group's name.
		 *
		 * Reads the source because the rendered page looks the same while the
		 * template check holds.
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
