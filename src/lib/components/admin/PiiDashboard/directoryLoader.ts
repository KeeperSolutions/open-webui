import { writable, type Readable } from 'svelte/store';
import { getAllUsers } from '$lib/apis/users';
import { describeLoadError, type DirectoryUser } from './sections/costAnalytics';

/** The OWUI user directory, as every section that names a user consumes it. */
export type DirectoryState = {
	users: DirectoryUser[];
	loading: boolean;
	failed: boolean;
	errorDetail: string | null;
};

/**
 * The one call this loader makes. Injectable so the error and empty paths can be
 * driven from tests without a network.
 */
export type DirectoryFetcher = (signal: AbortSignal) => Promise<{ users?: DirectoryUser[] } | null>;

export type DirectoryLoader = Readable<DirectoryState> & {
	/** Fetch the directory. Re-callable: a retry clears the previous failure. */
	load: () => Promise<void>;
	/** Abort what is in flight; the caller is going away and wants no more state. */
	destroy: () => void;
};

const INITIAL: DirectoryState = {
	users: [],
	loading: true,
	failed: false,
	errorDetail: null
};

const defaultFetcher: DirectoryFetcher = (signal) => getAllUsers(localStorage.token, signal);

export function createDirectoryLoader(fetcher: DirectoryFetcher = defaultFetcher): DirectoryLoader {
	const { subscribe, update } = writable<DirectoryState>({ ...INITIAL });

	let inFlight: AbortController | null = null;

	const load = async () => {
		// ⚠️ Supersede, like the other two loaders. The earlier reasoning here — "the
		// request takes no parameters, so two concurrent loads cannot disagree" — was
		// wrong: they cannot disagree about `users`, but they can disagree about
		// `failed`. Retry fires while the first load is still in flight (the button
		// appears as soon as EITHER metrics or the directory fails), and a slow
		// first attempt rejecting after a fast second one succeeded would blank the
		// table and show an error that no longer applies.
		inFlight?.abort();
		const controller = new AbortController();
		inFlight = controller;

		update((s) => ({ ...s, loading: true, failed: false, errorDetail: null }));

		try {
			const res = await fetcher(controller.signal);
			if (controller.signal.aborted) return;

			// A directory that answers with nothing is an empty directory, not a
			// failure — matching what the section did before this loader existed.
			update((s) => ({
				...s,
				users: res?.users ?? [],
				failed: false,
				errorDetail: null
			}));
		} catch (e: unknown) {
			if (controller.signal.aborted) return;
			update((s) => ({ ...s, failed: true, errorDetail: describeLoadError(e), users: [] }));
		} finally {
			if (!controller.signal.aborted) update((s) => ({ ...s, loading: false }));
		}
	};

	const destroy = () => {
		inFlight?.abort();
		inFlight = null;
	};

	return { subscribe, load, destroy };
}
