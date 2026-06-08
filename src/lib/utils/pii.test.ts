import { describe, it, expect } from 'vitest';
import { getPiiMaskingDefault } from './pii';

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
		expect(getPiiMaskingDefault({ pipelines: { valves: { other_filter: { pii_masking_enabled: false } } } })).toBe(true);
	});

	it('ignores a string pii_masking_enabled (malformed data)', () => {
		expect(getPiiMaskingDefault({ pipelines: { valves: { pii_filter: { pii_masking_enabled: 'yes' } } } })).toBe(true);
	});

	it('ignores a numeric pii_masking_enabled (malformed data)', () => {
		expect(getPiiMaskingDefault({ pipelines: { valves: { pii_filter: { pii_masking_enabled: 1 } } } })).toBe(true);
	});

	it('ignores a null pii_masking_enabled (malformed data)', () => {
		expect(getPiiMaskingDefault({ pipelines: { valves: { pii_filter: { pii_masking_enabled: null } } } })).toBe(true);
	});

	it('reads false from pii_filter', () => {
		expect(getPiiMaskingDefault({ pipelines: { valves: { pii_filter: { pii_masking_enabled: false } } } })).toBe(false);
	});

	it('reads true from pii_filter', () => {
		expect(getPiiMaskingDefault({ pipelines: { valves: { pii_filter: { pii_masking_enabled: true } } } })).toBe(true);
	});

	it('reads false from pii_filter_pipeline when pii_filter is absent', () => {
		expect(getPiiMaskingDefault({ pipelines: { valves: { pii_filter_pipeline: { pii_masking_enabled: false } } } })).toBe(false);
	});

	it('reads true from pii_filter_pipeline when pii_filter is absent', () => {
		expect(getPiiMaskingDefault({ pipelines: { valves: { pii_filter_pipeline: { pii_masking_enabled: true } } } })).toBe(true);
	});

	it('prefers pii_filter over pii_filter_pipeline when both are present', () => {
		expect(getPiiMaskingDefault({
			pipelines: {
				valves: {
					pii_filter: { pii_masking_enabled: false },
					pii_filter_pipeline: { pii_masking_enabled: true }
				}
			}
		})).toBe(false);
	});
});
