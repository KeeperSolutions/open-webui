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

export function windowLabel(key: PeriodKey, customDays: number): string {
	switch (key) {
		case 'day':
			return '24h';
		case 'week':
			return '7d';
		case 'month':
			return '30d';
		case 'year':
			return '365d';
		case 'custom':
			return `${customDays}d`;
	}
}

export function toLangfuseParams(key: PeriodKey, customDays: number): { period: string; days?: number } {
	switch (key) {
		case 'day':
		case 'week':
		case 'month':
			return { period: key };
		case 'year':
			return { period: 'custom', days: 365 };
		case 'custom':
			return { period: 'custom', days: customDays };
	}
}
