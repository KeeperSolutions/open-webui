import { describe, it, expect } from 'vitest';
import { toLangfuseParams, formatWindow, periodLabel, PERIOD_KEYS } from './periods';

describe('toLangfuseParams', () => {
	it('maps Day to the backend `today` window, not `day` (which is yesterday)', () => {
		expect(toLangfuseParams('day', 7)).toEqual({ period: 'today' });
	});
	it('maps direct periods', () => {
		expect(toLangfuseParams('week', 7)).toEqual({ period: 'week' });
		expect(toLangfuseParams('month', 7)).toEqual({ period: 'month' });
	});
	it('maps year to custom 365', () => {
		expect(toLangfuseParams('year', 7)).toEqual({ period: 'custom', days: 365 });
	});
	it('passes custom days through', () => {
		expect(toLangfuseParams('custom', 14)).toEqual({ period: 'custom', days: 14 });
	});
});

describe('formatWindow', () => {
	it('collapses a single day to one date', () => {
		expect(formatWindow('2026-08-04T00:00:00Z', '2026-08-04T14:12:00Z')).toBe('4 Aug 2026');
	});
	it('omits the repeated month within one month', () => {
		expect(formatWindow('2026-07-01T00:00:00Z', '2026-07-31T23:59:59Z')).toBe('1 – 31 Jul 2026');
	});
	it('keeps both months when the window crosses a month boundary', () => {
		expect(formatWindow('2026-07-27T00:00:00Z', '2026-08-02T23:59:59Z')).toBe(
			'27 Jul – 2 Aug 2026'
		);
	});
	it('keeps both years when the window crosses a year boundary', () => {
		expect(formatWindow('2025-08-04T00:00:00Z', '2026-08-03T23:59:59Z')).toBe(
			'4 Aug 2025 – 3 Aug 2026'
		);
	});
	it('renders the backend UTC day, not the local-timezone day', () => {
		// Formatting in local time would name a day the backend never queried.
		// The two assertions cover opposite sides of UTC: the first breaks in
		// zones east of it (23:59:59Z rolls into the next local day), the second
		// in zones west of it (00:00:00Z falls back into the previous one).
		// Under TZ=UTC neither can fail, because there is no difference to catch
		// — so this is verified under TZ=Europe/Zagreb and TZ=America/Los_Angeles.
		expect(formatWindow('2026-07-01T00:00:00Z', '2026-07-31T23:59:59Z')).toBe('1 – 31 Jul 2026');
		expect(formatWindow('2026-08-04T00:00:00Z', '2026-08-04T23:59:59Z')).toBe('4 Aug 2026');
	});
	it('returns empty string when the window is missing or unparseable', () => {
		expect(formatWindow('', '')).toBe('');
		expect(formatWindow('not-a-date', 'also-not')).toBe('');
	});
});

describe('periodLabel', () => {
	it('capitalises each key', () => {
		expect(PERIOD_KEYS.map(periodLabel)).toEqual(['Day', 'Week', 'Month', 'Year', 'Custom']);
	});
});
