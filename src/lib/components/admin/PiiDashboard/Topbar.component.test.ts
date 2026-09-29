// @vitest-environment jsdom
/**
 * The title names the scope on screen, not the viewer's rights, so a team
 * owner never sees "Admin Dashboard" over their team's rows.
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
		expect(screen.queryByText('PII Protection — Admin Dashboard')).toBeNull();
	});

	// The title depends on `teamId`, not `mayAct`: an admin viewing one team is
	// still viewing a team.
	it('says Team Dashboard on a team address regardless of who is looking', () => {
		mount({ teamId: 'team-1', mayAct: true });
		expect(screen.getByText('PII Protection — Team Dashboard')).toBeTruthy();
	});

	it('defaults to the instance title when teamId is not passed at all', () => {
		mount();
		expect(screen.getByText('PII Protection — Admin Dashboard')).toBeTruthy();
	});
});
