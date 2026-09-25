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
		// The endpoint is owner-only and returns 404 for most users, so a failure
		// must resolve to null rather than surface as an error.
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
		// The menu is mounted twice, and both instances call on the same open.
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
		// A non-owner does not become one mid-session, so retrying would waste a
		// request on every menu open.
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
	 * Reads the menu source, because the store can be correct while the menu
	 * ignores it, and mounting `UserMenu` needs the whole sidebar and its stores.
	 */
	const source = readFileSync(
		resolve(process.cwd(), 'src/lib/components/layout/Sidebar/UserMenu.svelte'),
		'utf-8'
	);

	it('shows the entry only to somebody who owns a team', () => {
		expect(source).toContain('{#if $ownedTeamId}');
	});

	it('sends them to their OWN team', () => {
		// The page refuses a team the caller does not own, so any other id would
		// lead to a refusal.
		expect(source).toContain('goto(`/team/${$ownedTeamId}/pii-dashboard`)');
	});

	it('asks for the answer when the menu opens', () => {
		expect(source).toContain('loadOwnedTeamId(localStorage.token)');
	});

	it('asks only where billing is on', () => {
		// The endpoint belongs to billing. Without billing it returns 404 for
		// everyone, so the request would be wasted.
		const hook = source.slice(source.indexOf('const handleDropdownChange'));
		const call = hook.indexOf('loadOwnedTeamId(');
		expect(hook.slice(0, call)).toContain('enable_billing');
	});
});
