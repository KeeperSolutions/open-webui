import { getPipelines, getPipelinesList } from '$lib/apis';

// Known PII filter pipeline IDs across deployed instances:
//   "pii_filter"           — pii-filter repo (pii_filter.py)
//   "pii_filter_pipeline"  — pipelines-v4 repo + GCP deployment (pii_filter_pipeline.py)
// Masking only happens when one of these filter pipelines is wired up on a
// connected pipeline server (local or cloud).
export const PII_FILTER_IDS = ['pii_filter', 'pii_filter_pipeline'] as const;

/**
 * Scope PII detections for the masked-values card on a user message.
 *
 * The card mixes two sources of file PII:
 *  - the ingest scan (whole-file, authoritative) surfaced separately as fileItems, and
 *  - chat-time B2 detections (`fileId` set) that reflect exactly what was masked to the
 *    LLM at SEND time — the same signal ordinary text messages use.
 *
 * A file whose ingest scan owns the display (`ingestCoveredFileIds`) is shown via
 * fileItems only, so its B2 detections are dropped to avoid double-counting. A file
 * the ingest scan did NOT cover (e.g. masking was off at upload, then turned on before
 * send) falls back to its B2 detections — scoped to the files on THIS message so PII
 * from other turns' attachments doesn't bleed in. Message PII (no `fileId`) is always kept.
 */
export function scopeCardDetections<T extends { fileId?: string | null }>(
	detections: T[],
	ingestCoveredFileIds: Set<string>,
	messageFileIds: Set<string>
): T[] {
	return (detections ?? []).filter((d) => {
		if (d?.fileId == null) return true;
		if (ingestCoveredFileIds.has(d.fileId)) return false;
		return messageFileIds.has(d.fileId);
	});
}

export function getPiiMaskingDefault(settings: {
	pipelines?: { valves?: Record<string, Record<string, unknown>> };
}): boolean {
	const valves = settings?.pipelines?.valves ?? {};
	for (const id of PII_FILTER_IDS) {
		const v = valves?.[id]?.pii_masking_enabled;
		if (typeof v === 'boolean') return v;
	}
	return true;
}

// The pipeline list is an admin-only remote call. The settings banner and the
// chat-entry toast both consult it, often within seconds of each other, so we
// memoize the result briefly to avoid duplicate round-trips.
const CONFIGURED_CACHE_TTL_MS = 60_000;
let configuredCache: { value: boolean; at: number } | null = null;

/** Test-only: drop the memoized detection result. */
export function resetPiiPipelineConfiguredCache(): void {
	configuredCache = null;
}

/**
 * Whether a PII filter pipeline is registered on any connected pipeline server
 * (local or cloud). Admin-only: the underlying endpoints require an admin token.
 *
 * Returns `false` on any failure (server unreachable, no pipelines, error) —
 * the user-visible outcome is identical (no masking), so we warn either way.
 */
export async function isPiiPipelineConfigured(
	token: string,
	{ force = false }: { force?: boolean } = {}
): Promise<boolean> {
	if (!force && configuredCache && Date.now() - configuredCache.at < CONFIGURED_CACHE_TTL_MS) {
		return configuredCache.value;
	}

	let value = false;
	try {
		const sources = (await getPipelinesList(token)) ?? [];
		const lists = await Promise.all(
			sources.map((source: { idx: number | string }) =>
				getPipelines(token, String(source.idx)).catch(() => [])
			)
		);
		const ids = new Set<string>();
		for (const pipelines of lists) {
			for (const pipeline of pipelines ?? []) {
				if (pipeline?.id) ids.add(pipeline.id);
			}
		}
		value = PII_FILTER_IDS.some((id) => ids.has(id));
	} catch {
		value = false;
	}

	configuredCache = { value, at: Date.now() };
	return value;
}
