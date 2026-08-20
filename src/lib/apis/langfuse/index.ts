import { WEBUI_API_BASE_URL } from '$lib/constants';

export type MetricRow = {
	user: string;
	model: string;
	tokens: number;
	cost: number;
	observations: number;
};

export type MyUsage = {
	month: number;
	year: number;
	total_tokens: number;
	total_cost: number;
};

/** Rows plus the exact UTC window the backend queried for them. */
export type MetricsResponse = {
	from: string;
	to: string;
	rows: MetricRow[];
};

export const getLangfuseMetrics = async (
	token: string,
	period: string = 'week',
	days?: number,
	signal?: AbortSignal,
	// Last, and optional: every existing caller passes positionally and stops
	// before this, so adding it cannot silently shift an argument.
	teamId?: string | null
): Promise<MetricsResponse> => {
	let error = null;

	const params = new URLSearchParams({ period });
	if (period === 'custom' && days) {
		params.set('days', String(days));
	}
	if (teamId) {
		params.set('team_id', teamId);
	}

	const res = await fetch(`${WEBUI_API_BASE_URL}/langfuse/metrics?${params}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		},
		signal
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			// A superseded request is not a failure — the caller aborted it on purpose
			// and discards the result, so it must not surface as a console error.
			if (err?.name === 'AbortError') return null;
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getMyLangfuseUsage = async (token: string): Promise<MyUsage> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/langfuse/my-usage`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
