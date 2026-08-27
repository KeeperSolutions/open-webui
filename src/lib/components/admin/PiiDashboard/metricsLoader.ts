import { writable, type Readable } from 'svelte/store';
import { getLangfuseMetrics, type MetricRow, type MetricsResponse } from '$lib/apis/langfuse';
import { describeLoadError } from './sections/costAnalytics';
import { toLangfuseParams, type PeriodKey } from './periods';

/** Everything a section needs to render one period's worth of Langfuse metrics. */
export type MetricsState = {
	rows: MetricRow[];
	windowFrom: string;
	windowTo: string;
	loading: boolean;
	failed: boolean;
	errorDetail: string | null;
};

/**
 * The one call this loader makes. Injectable so the supersede and error paths
 * can be driven from tests without a network or a fake clock.
 */
export type MetricsFetcher = (
	period: string,
	days: number | undefined,
	signal: AbortSignal
) => Promise<MetricsResponse | null>;

export type MetricsLoader = Readable<MetricsState> & {
	/** Fetch the window named by this period, superseding anything in flight. */
	load: (period: PeriodKey, customDays: number) => Promise<void>;
	/** Abort what is in flight; the caller is going away and wants no more state. */
	destroy: () => void;
};

const INITIAL: MetricsState = {
	rows: [],
	windowFrom: '',
	windowTo: '',
	// The first load starts before anything can subscribe, so the initial state
	// is already the loading one — a section must never flash "no data" first.
	loading: true,
	failed: false,
	errorDetail: null
};

/**
 * ⚠️ `teamId` is the LAST parameter, behind the fetcher, and the default fetcher is
 * built here rather than at module scope so it can close over it.
 *
 * Behind the fetcher because every existing call passes one positionally
 * (`createMetricsLoader(vi.fn())`); a leading `teamId` would shift all of them
 * silently. The fetcher TYPE is unchanged for the same reason.
 *
 * A consequence worth naming: an INJECTED fetcher never sees `teamId`, so a test
 * that supplies its own fetcher cannot prove the id reaches the API. That proof
 * has to mock the API module instead - see the propagation test below this file.
 */
export function createMetricsLoader(
	fetcher?: MetricsFetcher,
	teamId: string | null = null
): MetricsLoader {
	const fetchPeriod: MetricsFetcher =
		fetcher ??
		((period, days, signal) =>
			getLangfuseMetrics(localStorage.token, period, days, signal, teamId));

	const { subscribe, update } = writable<MetricsState>({ ...INITIAL });

	let inFlight: AbortController | null = null;

	const fail = (errorDetail: string | null) =>
		update((s) => ({
			...s,
			failed: true,
			errorDetail,
			rows: [],
			// The window described the previous period, and the topbar renders it as
			// authoritative once loading ends — so it goes with the rows.
			windowFrom: '',
			windowTo: ''
		}));

	const load = async (period: PeriodKey, customDays: number) => {
		// A newer period selection supersedes whatever is still in flight: abort it
		// so its (possibly slower) response cannot overwrite the fresher state.
		inFlight?.abort();
		const controller = new AbortController();
		inFlight = controller;

		update((s) => ({ ...s, loading: true, failed: false, errorDetail: null }));

		try {
			const { period: p, days } = toLangfuseParams(period, customDays);
			const res = await fetchPeriod(p, days, controller.signal);
			if (controller.signal.aborted) return;

			// The client resolves to null when the request never produced a body it
			// could throw about — an aborted fetch, or a network-layer failure whose
			// error carries no `detail`. Absorbed here so no section ever sees null:
			// the abort case left above, so what remains is a failure without a
			// message rather than a null dereference reported as the message.
			if (!res) {
				fail(null);
				return;
			}

			update((s) => ({
				...s,
				rows: res.rows,
				windowFrom: res.from,
				windowTo: res.to,
				failed: false,
				errorDetail: null
			}));
		} catch (e: unknown) {
			if (controller.signal.aborted) return;
			fail(describeLoadError(e));
		} finally {
			// Superseded loads leave `loading` alone — the newer one owns it.
			if (!controller.signal.aborted) update((s) => ({ ...s, loading: false }));
		}
	};

	const destroy = () => {
		inFlight?.abort();
		inFlight = null;
	};

	return { subscribe, load, destroy };
}
