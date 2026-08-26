import { get } from 'svelte/store';
import { getPipelines, getPipelinesList } from '$lib/apis';
import { config } from '$lib/stores';

// Known PII filter pipeline IDs across deployed instances:
//   "pii_filter"           — pii-filter repo (pii_filter.py)
//   "pii_filter_pipeline"  — pipelines-v4 repo + GCP deployment (pii_filter_pipeline.py)
// Masking only happens when one of these filter pipelines is wired up on a
// connected pipeline server (local or cloud).
//
// This is the FALLBACK. The authoritative list is the backend's PII_FILTER_IDS,
// served as `features.pii_filter_ids` — see `piiFilterIds()`.
export const PII_FILTER_IDS = ['pii_filter', 'pii_filter_pipeline'] as const;

/**
 * The ids the backend treats as mandatory PII filters.
 *
 * Read from `/api/config` so the frontend stops keeping a hand-copy of an env
 * variable an operator can override. Falls back to the constant above when the
 * field is absent (older backend) or malformed — a wrong list here would make
 * the masking column assert something untrue, so anything unexpected is ignored
 * rather than trusted.
 */
export function piiFilterIds(): readonly string[] {
	const fromConfig = get(config)?.features?.pii_filter_ids;
	if (
		Array.isArray(fromConfig) &&
		fromConfig.length > 0 &&
		fromConfig.every((id) => typeof id === 'string' && id.trim() !== '')
	) {
		return fromConfig;
	}
	return PII_FILTER_IDS;
}

/**
 * A user's masking default, from their stored pipeline valves.
 *
 * ⚠️ Reading the `config` store makes this function **impure**: its answer
 * depends on ambient state its signature does not mention. `buildRows` in the
 * PII dashboard calls it, so that pure, unit-tested function is now indirectly
 * store-dependent without saying so.
 *
 * The preferred shape is an explicit parameter — `getPiiMaskingDefault(settings, ids)`
 * — with callers passing the list down. It was not taken here because threading
 * the ids through `buildRows` means changing that module, which was out of scope
 * for this change. Take that route when the signature is next touched.
 */
export function getPiiMaskingDefault(settings: {
	pipelines?: { valves?: Record<string, Record<string, unknown>> };
}): boolean {
	const valves = settings?.pipelines?.valves ?? {};
	for (const id of piiFilterIds()) {
		const v = valves?.[id]?.pii_masking_enabled;
		if (typeof v === 'boolean') return v;
	}
	return true;
}

/** A user's stored masking preference, with "never chose" kept distinct. */
export type StoredPiiMasking = boolean | 'unset';

/**
 * What the user actually stored — `true`, `false`, or `'unset'` when they have
 * never touched the setting.
 *
 * ⚠️ Deliberately NOT a variant of `getPiiMaskingDefault`, which collapses
 * `'unset'` into `true`. That collapse is correct for the enforcement path,
 * which only needs the effective value; it is wrong for a governance report,
 * which must tell "chose protection" apart from "never chose". Two questions,
 * two functions — `getPiiMaskingDefault` stays untouched.
 *
 * ⚠️ `'unset'` does NOT mean unprotected. With no stored valve the backend sends
 * no key and the pipeline defaults to masking ON, so these users ARE masked.
 * Anything rendering this must treat `'unset'` as an on-state, never as a risk.
 *
 * Same id traversal as `getPiiMaskingDefault`: the first configured filter id
 * carrying a boolean wins, so both functions agree on which value is "the"
 * stored one.
 */
export function getStoredPiiMasking(settings: {
	pipelines?: { valves?: Record<string, Record<string, unknown>> };
}): StoredPiiMasking {
	const valves = settings?.pipelines?.valves ?? {};
	for (const id of piiFilterIds()) {
		const v = valves?.[id]?.pii_masking_enabled;
		if (typeof v === 'boolean') return v;
	}
	return 'unset';
}

/**
 * The value a chat request should carry in `features.pii_masking`.
 *
 * ⚠️ This is the value SENT TO THE SERVER, not what the control displays and not
 * what gets stored. Named `...ForRequest` so the three never get conflated: the
 * displayed state lives in the components, the stored preference lives in
 * `user.settings`, and the policy is layered over both without ever writing to
 * either.
 *
 * Team policy wins over the per-conversation toggle, so policy beats the
 * per-chat value. The backend enforces this independently — but relying on that would
 * leave this branch untested and dependent on the other side never regressing,
 * so the rule is stated once, here, and covered by tests.
 *
 * Pure on purpose: both inputs are explicit, unlike `getPiiMaskingDefault` above.
 *
 * @param policyEnforced team policy makes masking mandatory for this user
 * @param userChoice     the user's own per-conversation toggle
 */
export function piiMaskingForRequest(policyEnforced: boolean, userChoice: boolean): boolean {
	return policyEnforced || userChoice;
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
		value = piiFilterIds().some((id) => ids.has(id));
	} catch {
		value = false;
	}

	configuredCache = { value, at: Date.now() };
	return value;
}
