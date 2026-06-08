const PII_FILTER_IDS = ['pii_filter', 'pii_filter_pipeline'] as const;

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
