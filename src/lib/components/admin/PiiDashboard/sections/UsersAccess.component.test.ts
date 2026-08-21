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
import { render, screen } from '@testing-library/svelte';
import { readable } from 'svelte/store';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/apis/groups', () => ({ addUserToGroup: vi.fn(), removeUserFromGroup: vi.fn() }));

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
			policyGroups: [{ id: 'g1', name: 'Global PII Policy' }],
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
				{ id: 'g1', name: 'Global PII Policy' },
				{ id: 'g2', name: 'Second Policy' }
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

	const mountEmpty = (teamOnlyPolicyGroups: number) =>
		render(UsersAccess, {
			props: {
				users: [account()],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				policyGroups: [],
				mayAct: true,
				teamOnlyPolicyGroups
			},
			context: new Map([['i18n', i18n]])
		});

	it('says nothing enforces masking when nothing does', () => {
		mountEmpty(0);
		expect(screen.getByTitle(OLD)).toBeTruthy();
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
				users: [
					account({ pii_masking_enforced: true, pii_policy_group_ids: policyGroupIds })
				],
				metricRows: [],
				loading: false,
				failed: false,
				onRetry: () => {},
				policyGroups: [
					{ id: TEAM, name: 'PII — Acme · abcdef01' },
					{ id: OTHER, name: 'Global PII Policy' }
				],
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
