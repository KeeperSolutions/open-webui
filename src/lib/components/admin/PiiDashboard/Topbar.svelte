<script lang="ts">
	import { getContext } from 'svelte';
	import PeriodPill from './parts/PeriodPill.svelte';
	import Toggle from './parts/Toggle.svelte';
	import HgIconShield from '$lib/components/icons/HgIconShield.svelte';
	import { PERIOD_KEYS, periodLabel, type PeriodKey } from './periods';

	const i18n = getContext('i18n');

	export let period: PeriodKey = 'week';
	export let customDays = 7;
</script>

<div class="flex flex-wrap items-center justify-between gap-3 font-['Inter']">
	<div class="text-lg font-bold leading-[1.55] text-pii-ink">
		{$i18n.t('PII Protection — Admin Dashboard')}
	</div>

	<div class="flex items-center gap-2">
		<div class="flex items-center gap-2">
			{#each PERIOD_KEYS as key}
				<PeriodPill active={period === key} on:click={() => (period = key)}>
					{$i18n.t(periodLabel(key))}{key === 'custom' ? ' ▾' : ''}
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
		</div>

		<!-- TODO(TRAU-533 §D13): global PII masking toggle is a mutation with no
		     agreed backend; rendered disabled until that decision lands. -->
		<div
			class="flex items-center gap-2 rounded-full border border-pii-line bg-pii-white px-3.5 py-2"
			title={$i18n.t('Global PII masking control is not yet available')}
		>
			<HgIconShield class="size-4 text-pii-ink" />
			<span class="text-[13px] font-medium text-pii-ink">{$i18n.t('PII Masking')}</span>
			<Toggle on disabled />
		</div>
	</div>
</div>
