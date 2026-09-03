import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/apis', () => ({
	getPipelinesList: vi.fn(),
	getPipelines: vi.fn()
}));

import { getPipelines, getPipelinesList } from '$lib/apis';
import { config } from '$lib/stores';
import {
	getPiiMaskingDefault,
	isPiiPipelineConfigured,
	resetPiiPipelineConfiguredCache,
	scopeCardDetections,
	piiFilterIds,
	getStoredPiiMasking,
	piiMaskingForRequest,
	PII_FILTER_IDS
} from './pii';

/** Puts a features payload into the config store, as /api/config would. */
const setConfig = (features: unknown) =>
	config.set(features === undefined ? undefined : ({ features } as never));

describe('piiFilterIds', () => {
	beforeEach(() => config.set(undefined));

	it('uses the list the backend serves', () => {
		setConfig({ pii_filter_ids: ['only_this_one'] });
		expect(piiFilterIds()).toEqual(['only_this_one']);
	});

	it('falls back to the built-in list when config has not loaded', () => {
		expect(piiFilterIds()).toEqual(PII_FILTER_IDS);
	});

	it('falls back when the field is absent — an older backend', () => {
		setConfig({ enable_admin_analytics: true });
		expect(piiFilterIds()).toEqual(PII_FILTER_IDS);
	});

	it('falls back on an empty list rather than granting nothing', () => {
		setConfig({ pii_filter_ids: [] });
		expect(piiFilterIds()).toEqual(PII_FILTER_IDS);
	});

	it.each([
		['a string instead of a list', 'pii_filter'],
		['a list with a non-string', ['pii_filter', 7]],
		['a list with a blank id', ['pii_filter', '   ']],
		['null', null]
	])('falls back on %s', (_label, value) => {
		setConfig({ pii_filter_ids: value });
		expect(piiFilterIds()).toEqual(PII_FILTER_IDS);
	});
});

describe('getPiiMaskingDefault reads the served list', () => {
	beforeEach(() => config.set(undefined));

	it('honours a valve for an id only the backend knows about', () => {
		setConfig({ pii_filter_ids: ['custom_filter'] });
		expect(
			getPiiMaskingDefault({
				pipelines: { valves: { custom_filter: { pii_masking_enabled: false } } }
			})
		).toBe(false);
	});

	it('ignores a valve for an id the backend no longer lists', () => {
		// The operator narrowed PII_FILTER_IDS; a stale valve must not decide.
		setConfig({ pii_filter_ids: ['pii_filter_pipeline'] });
		expect(
			getPiiMaskingDefault({
				pipelines: { valves: { pii_filter: { pii_masking_enabled: false } } }
			})
		).toBe(true);
	});

	it('uses the built-in list when config is unavailable', () => {
		expect(
			getPiiMaskingDefault({
				pipelines: { valves: { pii_filter: { pii_masking_enabled: false } } }
			})
		).toBe(false);
	});
});

describe('getPiiMaskingDefault', () => {
	it('defaults to true when settings is empty', () => {
		expect(getPiiMaskingDefault({})).toBe(true);
	});

	it('defaults to true when pipelines key is absent', () => {
		expect(getPiiMaskingDefault({ pipelines: undefined })).toBe(true);
	});

	it('defaults to true when valves are absent', () => {
		expect(getPiiMaskingDefault({ pipelines: {} })).toBe(true);
	});

	it('defaults to true when neither known filter ID is present', () => {
		expect(
			getPiiMaskingDefault({
				pipelines: { valves: { other_filter: { pii_masking_enabled: false } } }
			})
		).toBe(true);
	});

	it('ignores a string pii_masking_enabled (malformed data)', () => {
		expect(
			getPiiMaskingDefault({
				pipelines: { valves: { pii_filter: { pii_masking_enabled: 'yes' } } }
			})
		).toBe(true);
	});

	it('ignores a numeric pii_masking_enabled (malformed data)', () => {
		expect(
			getPiiMaskingDefault({ pipelines: { valves: { pii_filter: { pii_masking_enabled: 1 } } } })
		).toBe(true);
	});

	it('ignores a null pii_masking_enabled (malformed data)', () => {
		expect(
			getPiiMaskingDefault({ pipelines: { valves: { pii_filter: { pii_masking_enabled: null } } } })
		).toBe(true);
	});

	it('reads false from pii_filter', () => {
		expect(
			getPiiMaskingDefault({
				pipelines: { valves: { pii_filter: { pii_masking_enabled: false } } }
			})
		).toBe(false);
	});

	it('reads true from pii_filter', () => {
		expect(
			getPiiMaskingDefault({ pipelines: { valves: { pii_filter: { pii_masking_enabled: true } } } })
		).toBe(true);
	});

	it('reads false from pii_filter_pipeline when pii_filter is absent', () => {
		expect(
			getPiiMaskingDefault({
				pipelines: { valves: { pii_filter_pipeline: { pii_masking_enabled: false } } }
			})
		).toBe(false);
	});

	it('reads true from pii_filter_pipeline when pii_filter is absent', () => {
		expect(
			getPiiMaskingDefault({
				pipelines: { valves: { pii_filter_pipeline: { pii_masking_enabled: true } } }
			})
		).toBe(true);
	});

	it('prefers pii_filter over pii_filter_pipeline when both are present', () => {
		expect(
			getPiiMaskingDefault({
				pipelines: {
					valves: {
						pii_filter: { pii_masking_enabled: false },
						pii_filter_pipeline: { pii_masking_enabled: true }
					}
				}
			})
		).toBe(false);
	});
});

describe('isPiiPipelineConfigured', () => {
	const mockList = vi.mocked(getPipelinesList);
	const mockGet = vi.mocked(getPipelines);

	beforeEach(() => {
		vi.clearAllMocks();
		resetPiiPipelineConfiguredCache();
		// These cases assert against the built-in list; an earlier describe may
		// have left a config behind.
		config.set(undefined);
	});

	it('returns true when a source hosts the pii_filter pipeline', async () => {
		mockList.mockResolvedValue([{ url: 'http://local', idx: 0 }]);
		mockGet.mockResolvedValue([{ id: 'other' }, { id: 'pii_filter' }]);

		expect(await isPiiPipelineConfigured('tok')).toBe(true);
	});

	it('returns true for the pii_filter_pipeline id', async () => {
		mockList.mockResolvedValue([{ url: 'http://cloud', idx: 1 }]);
		mockGet.mockResolvedValue([{ id: 'pii_filter_pipeline' }]);

		expect(await isPiiPipelineConfigured('tok')).toBe(true);
	});

	it('detects an id only the served list knows about', async () => {
		setConfig({ pii_filter_ids: ['custom_filter'] });
		mockList.mockResolvedValue([{ url: 'http://local', idx: 0 }]);
		mockGet.mockResolvedValue([{ id: 'custom_filter' }]);

		expect(await isPiiPipelineConfigured('tok')).toBe(true);
	});

	it('ignores a built-in id the served list no longer contains', async () => {
		// Operator narrowed PII_FILTER_IDS; a pipeline outside that list is not
		// the one enforcement runs on, so it must not count as configured.
		setConfig({ pii_filter_ids: ['pii_filter_pipeline'] });
		mockList.mockResolvedValue([{ url: 'http://local', idx: 0 }]);
		mockGet.mockResolvedValue([{ id: 'pii_filter' }]);

		expect(await isPiiPipelineConfigured('tok')).toBe(false);
	});

	it('falls back to the built-in list when config carries no field', async () => {
		setConfig({ enable_admin_analytics: true });
		mockList.mockResolvedValue([{ url: 'http://local', idx: 0 }]);
		mockGet.mockResolvedValue([{ id: 'pii_filter' }]);

		expect(await isPiiPipelineConfigured('tok')).toBe(true);
	});

	it('returns false when no source hosts a PII filter pipeline', async () => {
		mockList.mockResolvedValue([{ url: 'http://local', idx: 0 }]);
		mockGet.mockResolvedValue([{ id: 'some_other_filter' }]);

		expect(await isPiiPipelineConfigured('tok')).toBe(false);
	});

	it('returns false when no pipeline sources are connected', async () => {
		mockList.mockResolvedValue([]);

		expect(await isPiiPipelineConfigured('tok')).toBe(false);
		expect(mockGet).not.toHaveBeenCalled();
	});

	it('returns false when the pipeline list endpoint throws (server unreachable)', async () => {
		mockList.mockRejectedValue(new Error('connection refused'));

		expect(await isPiiPipelineConfigured('tok')).toBe(false);
	});

	it('aggregates across sources — finds the filter on a later source', async () => {
		mockList.mockResolvedValue([
			{ url: 'http://local', idx: 0 },
			{ url: 'http://cloud', idx: 1 }
		]);
		mockGet.mockImplementation(async (_token: string, urlIdx?: string) =>
			urlIdx === '1' ? [{ id: 'pii_filter' }] : [{ id: 'noop' }]
		);

		expect(await isPiiPipelineConfigured('tok')).toBe(true);
	});

	it('tolerates one source failing and still finds the filter on another', async () => {
		mockList.mockResolvedValue([
			{ url: 'http://local', idx: 0 },
			{ url: 'http://cloud', idx: 1 }
		]);
		mockGet.mockImplementation(async (_token: string, urlIdx?: string) => {
			if (urlIdx === '0') throw new Error('down');
			return [{ id: 'pii_filter' }];
		});

		expect(await isPiiPipelineConfigured('tok')).toBe(true);
	});

	it('memoizes the result and bypasses the cache with force', async () => {
		mockList.mockResolvedValue([{ url: 'http://local', idx: 0 }]);
		mockGet.mockResolvedValue([{ id: 'pii_filter' }]);

		expect(await isPiiPipelineConfigured('tok')).toBe(true);
		// Second call within TTL must not hit the API again.
		expect(await isPiiPipelineConfigured('tok')).toBe(true);
		expect(mockList).toHaveBeenCalledTimes(1);

		// force re-queries.
		await isPiiPipelineConfigured('tok', { force: true });
		expect(mockList).toHaveBeenCalledTimes(2);
	});
});

describe('scopeCardDetections', () => {
	const msg = { type: 'PERSON', start: 0, end: 3 }; // message PII (no fileId)
	const fileA = { type: 'EMAIL', start: 0, end: 5, fileId: 'a' };
	const fileB = { type: 'PHONE', start: 0, end: 5, fileId: 'b' };

	it('always keeps message PII (no fileId)', () => {
		expect(scopeCardDetections([msg], new Set(), new Set())).toEqual([msg]);
	});

	it('drops file PII whose ingest scan owns the display (avoids double-count)', () => {
		// file "a" is ingest-covered -> fileItems is authoritative, so B2 is dropped.
		expect(scopeCardDetections([fileA], new Set(['a']), new Set(['a']))).toEqual([]);
	});

	it('keeps file PII (B2 fallback) when ingest did NOT cover the file', () => {
		// toggle off at upload -> no ingest scan -> B2 is the only source -> keep it.
		expect(scopeCardDetections([fileA], new Set(), new Set(['a']))).toEqual([fileA]);
	});

	it('drops file PII for a file not attached to this message (scoping)', () => {
		// "b" was resent in the turn but is not on THIS user message.
		expect(scopeCardDetections([fileB], new Set(), new Set(['a']))).toEqual([]);
	});

	it('handles a mixed batch', () => {
		const covered = new Set(['a']); // a via ingest, b falls back to B2
		const onMessage = new Set(['a', 'b']);
		expect(scopeCardDetections([msg, fileA, fileB], covered, onMessage)).toEqual([msg, fileB]);
	});

	it('returns [] for nullish input', () => {
		expect(scopeCardDetections(undefined as never, new Set(), new Set())).toEqual([]);
	});
});

describe('piiMaskingForRequest', () => {
	// The value SENT to the server. Team policy wins over the per-conversation
	// toggle. The backend enforces the same rule independently; this keeps the
	// frontend from quietly disagreeing.

	it('policy ON overrides a user who switched masking off', () => {
		expect(piiMaskingForRequest(true, false)).toBe(true);
	});

	it('policy OFF lets the user switch masking off', () => {
		expect(piiMaskingForRequest(false, false)).toBe(false);
	});

	it('policy OFF lets the user keep masking on', () => {
		expect(piiMaskingForRequest(false, true)).toBe(true);
	});

	it('policy ON with the user already on stays on', () => {
		expect(piiMaskingForRequest(true, true)).toBe(true);
	});

	it('never returns false while the policy is enforced', () => {
		for (const userChoice of [true, false]) {
			expect(piiMaskingForRequest(true, userChoice)).toBe(true);
		}
	});
});

describe('getStoredPiiMasking', () => {
	beforeEach(() => setConfig(undefined));

	it("reports 'unset' when the user never stored anything", () => {
		expect(getStoredPiiMasking({})).toBe('unset');
		expect(getStoredPiiMasking({ pipelines: { valves: {} } })).toBe('unset');
	});

	it('reports a stored false as false, not as unset', () => {
		expect(
			getStoredPiiMasking({ pipelines: { valves: { pii_filter: { pii_masking_enabled: false } } } })
		).toBe(false);
	});

	it('reports a stored true as true', () => {
		expect(
			getStoredPiiMasking({ pipelines: { valves: { pii_filter: { pii_masking_enabled: true } } } })
		).toBe(true);
	});

	it('ignores non-boolean values, treating them as never stored', () => {
		expect(
			getStoredPiiMasking({ pipelines: { valves: { pii_filter: { pii_masking_enabled: 'yes' } } } })
		).toBe('unset');
	});

	it('falls through to the second configured filter id', () => {
		expect(
			getStoredPiiMasking({
				pipelines: { valves: { pii_filter_pipeline: { pii_masking_enabled: false } } }
			})
		).toBe(false);
	});

	it('ignores a valve for an id outside the configured list', () => {
		expect(
			getStoredPiiMasking({ pipelines: { valves: { other: { pii_masking_enabled: false } } } })
		).toBe('unset');
	});

	it('leaves getPiiMaskingDefault untouched: it still collapses unset to true', () => {
		// The two answer different questions and must not converge.
		expect(getPiiMaskingDefault({})).toBe(true);
		expect(getStoredPiiMasking({})).toBe('unset');
	});
});
