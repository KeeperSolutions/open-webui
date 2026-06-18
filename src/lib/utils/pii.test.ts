import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/apis', () => ({
	getPipelinesList: vi.fn(),
	getPipelines: vi.fn()
}));

import { getPipelines, getPipelinesList } from '$lib/apis';
import {
	getPiiMaskingDefault,
	isPiiPipelineConfigured,
	resetPiiPipelineConfiguredCache
} from './pii';

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
