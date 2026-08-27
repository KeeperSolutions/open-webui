// @vitest-environment jsdom
/**
 * The title names the SCOPE the screen is showing, not the viewer's rights.
 *
 * ⚠️ This was a real defect (G-A6, N-3): a team owner — never an admin — read
 * "PII Protection — Admin Dashboard" over their own team's rows. Decision 2 says
 * the address selects a scope, not a permission, and the heading is the one place
 * that sentence is visible to the person reading it.
 *
 * The title lives entirely in markup, so nothing else can cover it.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { readable } from 'svelte/store';

import Topbar from './Topbar.svelte';

const i18n = readable({ t: (k: string) => k });

const mount = (props: Record<string, unknown> = {}) =>
	render(Topbar, {
		props: { period: 'week', customDays: 7, windowFrom: '', windowTo: '', ...props },
		context: new Map([['i18n', i18n]])
	});

describe('Topbar title', () => {
	it('says Admin Dashboard when no team is addressed', () => {
		mount({ teamId: null });
		expect(screen.getByText('PII Protection — Admin Dashboard')).toBeTruthy();
		expect(screen.queryByText('PII Protection — Team Dashboard')).toBeNull();
	});

	it('says Team Dashboard when a team is addressed', () => {
		mount({ teamId: 'team-1' });
		expect(screen.getByText('PII Protection — Team Dashboard')).toBeTruthy();
		// The word that made a team owner look like an administrator.
		expect(screen.queryByText('PII Protection — Admin Dashboard')).toBeNull();
	});

	/**
	 * ⚠️ The whole point of keying the title off `teamId` rather than `mayAct`:
	 * an admin reading one team is still reading ONE TEAM. If this ever flips to
	 * a permission check, this is the test that dies.
	 */
	it('says Team Dashboard on a team address regardless of who is looking', () => {
		mount({ teamId: 'team-1', mayAct: true });
		expect(screen.getByText('PII Protection — Team Dashboard')).toBeTruthy();
	});

	it('defaults to the instance title when teamId is not passed at all', () => {
		mount();
		expect(screen.getByText('PII Protection — Admin Dashboard')).toBeTruthy();
	});
});
