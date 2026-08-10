<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import Topbar from './Topbar.svelte';
	import CostAnalytics from './sections/CostAnalytics.svelte';
	import UsersAccess from './sections/UsersAccess.svelte';
	import { createMetricsLoader } from './metricsLoader';
	import { createDirectoryLoader } from './directoryLoader';
	import { createUsersAccessLoader } from './usersAccessLoader';
	import type { PeriodKey } from './periods';

	let period: PeriodKey = 'week';
	let customDays = 7;

	const metrics = createMetricsLoader();
	const directory = createDirectoryLoader();
	const usersAccess = createUsersAccessLoader();

	// Reload whenever the period selection changes.
	$: metrics.load(period, customDays);

	// Neither directory is in that reactive statement: neither has a notion of a
	// window, so both are fetched once and reused by every period.
	onMount(() => {
		directory.load();
		usersAccess.load();
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

	// Section 4 combines differently on purpose. Its subject is the directory —
	// who exists, what they may reach, whether their masking is on — and all of
	// that comes from the local DB. Spend is a second, slower source from an
	// external service, so a Langfuse outage degrades one column instead of
	// hiding the governance facts the section exists to show.
	$: usersLoading = $usersAccess.loading;
	$: usersFailed = $usersAccess.failed;
	// Once spend has arrived at least once, a later fetch shows the previous
	// window dimmed rather than blanking it; before that there is nothing true
	// to dim, so the columns read as unknown.
	let costEverLoaded = false;
	$: if (!$metrics.loading && !$metrics.failed) costEverLoaded = true;
	$: costUnknown = $metrics.failed || !costEverLoaded;
	$: costStale = $metrics.loading && costEverLoaded;

	const retry = () => {
		metrics.load(period, customDays);
		directory.load();
	};

	onDestroy(() => {
		metrics.destroy();
		directory.destroy();
		usersAccess.destroy();
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
		<UsersAccess
			users={$usersAccess.users}
			metricRows={$metrics.rows}
			truncated={$usersAccess.truncatedUsers}
			models={$usersAccess.models}
			truncatedModels={$usersAccess.truncatedModels}
			loading={usersLoading}
			failed={usersFailed}
			errorDetail={$usersAccess.errorDetail}
			{costUnknown}
			{costStale}
			onRetry={() => usersAccess.load()}
		/>
	</div>
</div>
