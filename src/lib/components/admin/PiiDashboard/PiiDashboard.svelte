<script lang="ts">
	import Topbar from './Topbar.svelte';
	import CostAnalytics from './sections/CostAnalytics.svelte';
	import type { PeriodKey } from './periods';

	let period: PeriodKey = 'week';
	let customDays = 7;
	// Reported by the section that fetched them; the topbar only renders them.
	let windowFrom = '';
	let windowTo = '';
	// While a fetch is in flight the window on screen still describes the previous
	// one, so the topbar dims it rather than asserting a range it cannot confirm.
	let loading = true;
</script>

<!-- Light-lock: neutralises .dark body globals; real dark mode is out of scope. -->
<div class="min-h-full bg-pii-bg text-pii-ink font-['Inter'] px-6 py-4">
	<div class="mx-auto flex max-w-[1190px] flex-col gap-6">
		<Topbar bind:period bind:customDays {windowFrom} {windowTo} windowStale={loading} />
		<CostAnalytics {period} {customDays} bind:windowFrom bind:windowTo bind:loading />
	</div>
</div>
