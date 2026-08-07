<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import Topbar from './Topbar.svelte';
	import CostAnalytics from './sections/CostAnalytics.svelte';
	import { createMetricsLoader } from './metricsLoader';
	import { createDirectoryLoader } from './directoryLoader';
	import type { PeriodKey } from './periods';

	let period: PeriodKey = 'week';
	let customDays = 7;

	const metrics = createMetricsLoader();
	const directory = createDirectoryLoader();

	// Reload whenever the period selection changes.
	$: metrics.load(period, customDays);

	// The directory is deliberately not in that reactive statement: it has no
	// notion of a window, so it is fetched once and reused by every period.
	onMount(() => {
		directory.load();
	});

	// One section's worth of state, combined from two independent loads so the
	// screen behaves as it did when a single try block covered both calls.
	$: loading = $metrics.loading || $directory.loading;
	$: failed = $metrics.failed || $directory.failed;
	// Metrics are the first thing a load needs, so theirs is the failure that gets
	// described; the directory's message surfaces only when the metrics arrived.
	$: errorDetail = $metrics.errorDetail ?? $directory.errorDetail;
	// A failed load vouches for neither the rows nor the window that preceded it,
	// and the topbar renders the window as authoritative once loading ends.
	$: rows = failed ? [] : $metrics.rows;
	$: windowFrom = failed ? '' : $metrics.windowFrom;
	$: windowTo = failed ? '' : $metrics.windowTo;

	const retry = () => {
		metrics.load(period, customDays);
		directory.load();
	};

	onDestroy(() => {
		metrics.destroy();
		directory.destroy();
	});
</script>

<!-- Light-lock: neutralises .dark body globals; real dark mode is out of scope. -->
<div class="min-h-full bg-pii-bg text-pii-ink font-['Inter'] px-6 py-4">
	<div class="mx-auto flex max-w-[1190px] flex-col gap-6">
		<Topbar bind:period bind:customDays {windowFrom} {windowTo} windowStale={loading} />
		<CostAnalytics
			{rows}
			users={$directory.users}
			{loading}
			{failed}
			{errorDetail}
			onRetry={retry}
		/>
	</div>
</div>
