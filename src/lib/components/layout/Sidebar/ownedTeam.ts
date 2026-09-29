/**
 * The id of the team the session user owns, or `null`.
 *
 * Fetched once per session and shared, because `Sidebar.svelte` mounts the
 * profile menu twice and both instances ask. `GET /billing/team` is the only
 * endpoint that answers this, and it is expensive: it builds the whole team
 * page (members, invites, usage ledger, credit balances) and looks the team up
 * by an unindexed owner column.
 */
import { writable, type Readable } from 'svelte/store';
import { getTeamStatus } from '$lib/apis/billing';

/** Only the field this module needs, so a test does not have to build a team. */
export type TeamStatusFetcher = (token: string) => Promise<{ team_id?: string } | null>;

const store = writable<string | null>(null);

/** `null` until asked, and `null` again for everyone who owns no team. */
export const ownedTeamId: Readable<string | null> = { subscribe: store.subscribe };

let inFlight: Promise<string | null> | null = null;

export function loadOwnedTeamId(
	token: string,
	fetcher: TeamStatusFetcher = getTeamStatus
): Promise<string | null> {
	// Both the promise and its result are cached: a second caller during the
	// request joins it, and a caller after it does not repeat it.
	if (inFlight) return inFlight;

	inFlight = fetcher(token)
		.then((status) => (typeof status?.team_id === 'string' ? status.team_id : null))
		// A failure means "owns no team", not an error. `GET /billing/team` is
		// owner-only and returns 404 for everyone else, so no toast is shown and
		// the menu entry stays hidden.
		.catch(() => null)
		.then((id) => {
			store.set(id);
			return id;
		});

	return inFlight;
}

/** Test seam. The app never clears this, because a session cannot change owner. */
export function resetOwnedTeamId(): void {
	inFlight = null;
	store.set(null);
}
