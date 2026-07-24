import { describe, it, expect } from 'vitest';
import { toLangfuseParams, windowLabel, periodLabel, PERIOD_KEYS } from './periods';

describe('toLangfuseParams', () => {
	it('maps direct periods', () => {
		expect(toLangfuseParams('day', 7)).toEqual({ period: 'day' });
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

describe('windowLabel', () => {
	it('renders the KPI window label', () => {
		expect(windowLabel('day', 7)).toBe('24h');
		expect(windowLabel('week', 7)).toBe('7d');
		expect(windowLabel('month', 7)).toBe('30d');
		expect(windowLabel('year', 7)).toBe('365d');
		expect(windowLabel('custom', 14)).toBe('14d');
	});
});

describe('periodLabel', () => {
	it('capitalises each key', () => {
		expect(PERIOD_KEYS.map(periodLabel)).toEqual(['Day', 'Week', 'Month', 'Year', 'Custom']);
	});
});
