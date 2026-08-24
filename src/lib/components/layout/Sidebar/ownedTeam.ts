/**
 * Does the person using this session OWN a team, and which one.
 *
 * Answered once per session and shared, because the profile menu is mounted
 * TWICE (`Sidebar.svelte:962` and `:1598`) and both instances ask. Two mounts
 * asking independently would double a request that is already not cheap:
 * `GET /billing/team` builds the whole team page — members, invites, the usage
 * ledger and credit balances — and resolves the team through
 * `Teams.get_by_owner_user_id`, a `.first()` over an UNINDEXED column. It is the
 * only endpoint that answers this question today; asking it once is the cost of
 * not adding another one.
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
		// ⚠️ A failure here is the ANSWER, not an error to report. `GET /billing/team`
		// is owner-only and 404s for everybody else, which is most people — surfacing
		// that would put a toast in front of a user who did nothing wrong. The menu
		// entry simply does not appear.
		.catch(() => null)
		.then((id) => {
			store.set(id);
			return id;
		});

	return inFlight;
}

/** Test seam. Nothing in the app clears this — a session cannot change owner. */
export function resetOwnedTeamId(): void {
	inFlight = null;
	store.set(null);
}
