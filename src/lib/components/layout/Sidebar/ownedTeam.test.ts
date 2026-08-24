import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { loadOwnedTeamId, ownedTeamId, resetOwnedTeamId } from './ownedTeam';

beforeEach(() => resetOwnedTeamId());

describe('ownedTeamId', () => {
	it('starts as null, before anybody has asked', () => {
		expect(get(ownedTeamId)).toBeNull();
	});

	it('publishes the id of the team the user owns', async () => {
		await loadOwnedTeamId('t', async () => ({ team_id: 'team-1' }));
		expect(get(ownedTeamId)).toBe('team-1');
	});

	it('stays null for somebody who owns no team', async () => {
		// ⚠️ The 404 path, which is MOST people. It has to be an answer, not an
		// error: the endpoint is owner-only, and a rejected promise here would put
		// a failure in front of every ordinary member who opens their own menu.
		await loadOwnedTeamId('t', async () => {
			throw new Error('404 Not Found');
		});
		expect(get(ownedTeamId)).toBeNull();
	});

	it('never rejects, whatever the fetcher does', async () => {
		await expect(
			loadOwnedTeamId('t', async () => {
				throw new Error('network down');
			})
		).resolves.toBeNull();
	});

	it('ignores a response with no team id', async () => {
		await loadOwnedTeamId('t', async () => ({}));
		expect(get(ownedTeamId)).toBeNull();
	});

	it('asks once, however many callers there are', async () => {
		// The menu is mounted twice, so this is the real shape rather than a
		// hypothetical: both instances call on the same open.
		const fetcher = vi.fn(async () => ({ team_id: 'team-1' }));
		await Promise.all([
			loadOwnedTeamId('t', fetcher),
			loadOwnedTeamId('t', fetcher),
			loadOwnedTeamId('t', fetcher)
		]);
		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	it('does not ask again after the answer has arrived', async () => {
		const fetcher = vi.fn(async () => ({ team_id: 'team-1' }));
		await loadOwnedTeamId('t', fetcher);
		await loadOwnedTeamId('t', fetcher);
		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	it('does not retry after a refusal either', async () => {
		// Deliberate: a non-owner is not going to become one mid-session, and
		// retrying would mean one wasted request per menu open, forever.
		const fetcher = vi.fn(async () => {
			throw new Error('404');
		});
		await loadOwnedTeamId('t', fetcher);
		await loadOwnedTeamId('t', fetcher);
		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	it('hands the token to the fetcher', () => {
		const fetcher = vi.fn(async () => ({ team_id: 'team-1' }));
		loadOwnedTeamId('the-token', fetcher);
		expect(fetcher).toHaveBeenCalledWith('the-token');
	});
});

describe('the menu entry this store exists for', () => {
	/**
	 * ⚠️ Structural, and load-bearing for the same reason as the dashboard's own
	 * prop witness: the store can be perfect while the menu ignores it. The
	 * component needs the whole sidebar, a config store, a session user and a
	 * dropdown to mount, so a rendered test would be proving those work.
	 */
	const source = readFileSync(
		resolve(process.cwd(), 'src/lib/components/layout/Sidebar/UserMenu.svelte'),
		'utf-8'
	);

	it('shows the entry only to somebody who owns a team', () => {
		expect(source).toContain('{#if $ownedTeamId}');
	});

	it('sends them to their OWN team', () => {
		// Not `/team/.../pii-dashboard` with anything else in the middle: the page
		// refuses a team the caller does not own, so a wrong id here is a menu item
		// that leads to a refusal.
		expect(source).toContain('goto(`/team/${$ownedTeamId}/pii-dashboard`)');
	});

	it('asks for the answer when the menu opens', () => {
		expect(source).toContain('loadOwnedTeamId(localStorage.token)');
	});

	it('asks only where billing is on', () => {
		// The endpoint is part of billing; without it the request 404s for
		// everybody and the entry can never appear, so asking would be pure waste.
		const hook = source.slice(source.indexOf('const handleDropdownChange'));
		const call = hook.indexOf('loadOwnedTeamId(');
		expect(hook.slice(0, call)).toContain('enable_billing');
	});
});
