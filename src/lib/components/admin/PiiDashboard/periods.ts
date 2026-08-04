import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';

dayjs.extend(utc);

export type PeriodKey = 'day' | 'week' | 'month' | 'year' | 'custom';

export const PERIOD_KEYS: readonly PeriodKey[] = ['day', 'week', 'month', 'year', 'custom'];

const LABELS: Record<PeriodKey, string> = {
	day: 'Day',
	week: 'Week',
	month: 'Month',
	year: 'Year',
	custom: 'Custom'
};

export function periodLabel(key: PeriodKey): string {
	return LABELS[key];
}

/**
 * Renders the window the backend actually queried.
 *
 * Boundaries are UTC calendar instants, so they are formatted in UTC too: in
 * a positive-offset zone (e.g. Zagreb) a local-time render of `…T00:00:00Z`
 * is still the same date, but `…T23:59:59Z` rolls over to the next day — the
 * topbar would then name a date the backend never queried.
 */
export function formatWindow(from: string, to: string): string {
	if (!from || !to) return '';
	const a = dayjs.utc(from);
	const b = dayjs.utc(to);
	if (!a.isValid() || !b.isValid()) return '';

	if (a.isSame(b, 'day')) return a.format('D MMM YYYY');
	if (a.isSame(b, 'year')) {
		return a.isSame(b, 'month')
			? `${a.format('D')} – ${b.format('D MMM YYYY')}`
			: `${a.format('D MMM')} – ${b.format('D MMM YYYY')}`;
	}
	return `${a.format('D MMM YYYY')} – ${b.format('D MMM YYYY')}`;
}

export function toLangfuseParams(key: PeriodKey, customDays: number): { period: string; days?: number } {
	switch (key) {
		// Backend `day` means *yesterday*; the Day pill is meant to show live
		// activity, so it maps to `today` (the current day so far).
		case 'day':
			return { period: 'today' };
		case 'week':
		case 'month':
			return { period: key };
		case 'year':
			return { period: 'custom', days: 365 };
		case 'custom':
			return { period: 'custom', days: customDays };
	}
}
