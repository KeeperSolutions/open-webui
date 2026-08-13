<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import PeriodPill from './parts/PeriodPill.svelte';
	import { PERIOD_KEYS, periodLabel, formatWindow, type PeriodKey } from './periods';

	const i18n: Writable<i18nType> = getContext('i18n');

	export let period: PeriodKey = 'week';
	export let customDays = 7;
	export let windowFrom = '';
	export let windowTo = '';
	/** The shown window belongs to the previous fetch; a newer one is in flight. */
	export let windowStale = false;

	$: windowRange = formatWindow(windowFrom, windowTo);
</script>

<div class="flex flex-wrap items-center justify-between gap-3 font-['Inter']">
	<div class="text-lg font-bold leading-[1.55] text-pii-ink">
		{$i18n.t('PII Protection — Admin Dashboard')}
	</div>

	<div class="flex flex-wrap items-center justify-end gap-2">
		<div class="flex flex-wrap items-center gap-2">
			{#each PERIOD_KEYS as key}
				<PeriodPill active={period === key} on:click={() => (period = key)}>
					{$i18n.t(periodLabel(key))}
				</PeriodPill>
			{/each}

			{#if period === 'custom'}
				<input
					type="number"
					min="1"
					bind:value={customDays}
					class="w-16 rounded-full border border-pii-line bg-pii-white px-3 py-[6px] text-[13px] text-pii-ink"
					aria-label={$i18n.t('Custom days')}
				/>
			{/if}

			<!-- The window the backend actually queried, not a restatement of the
			     pill — so it sits next to the controls that chose it, where a
			     custom day count can be read against the dates it resolved to.
			     Dimmed rather than hidden while refetching: removing it would
			     shift the pills on every period change. -->
			{#if windowRange}
				<span
					aria-busy={windowStale}
					class="ml-1 text-[12px] leading-[1.55] whitespace-nowrap text-pii-muted transition-opacity {windowStale
						? 'opacity-40'
						: 'opacity-100'}"
				>
					{windowRange}
				</span>
			{/if}
		</div>

		<!--
			The disabled "PII Masking" toggle that used to sit here is gone
			(TRAU-536 D-14). It claimed the decision had not been made, and it had:
			masking is enforced per group, its value is edited in the group's
			Permissions tab, and who falls under it is changed row by row in
			section 4. A dead control next to live ones reads as a broken feature,
			and an instance-wide switch would have contradicted the per-group model
			the policy actually uses.
		-->
	</div>
</div>
