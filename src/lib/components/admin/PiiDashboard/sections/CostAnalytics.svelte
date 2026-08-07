<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import SectionHeader from '../parts/SectionHeader.svelte';
	import KpiCard from '../parts/KpiCard.svelte';
	import BarRow from '../parts/BarRow.svelte';
	import Button from '../parts/Button.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import type { MetricRow } from '$lib/apis/langfuse';
	import { formatCostDisplay } from '$lib/apis/langfuse/tableUtils';
	import { formatNumber } from '$lib/utils';
	import {
		aggregateByModel,
		aggregateByUser,
		toBars,
		topModel,
		totals,
		inactiveUsers,
		type DirectoryUser
	} from './costAnalytics';

	const i18n: Writable<i18nType> = getContext('i18n');

	/** The metrics for the window the shell selected. Read-only: the shell owns
	 *  the fetch, so no two sections can disagree about which window is on screen. */
	export let rows: MetricRow[] = [];
	/** The OWUI directory, for naming users and counting the inactive ones. */
	export let users: DirectoryUser[] = [];
	export let loading = true;
	export let failed = false;
	/** What went wrong, when the failure said — shown under the generic message
	 *  rather than swallowed, since only an admin ever sees this screen. */
	export let errorDetail: string | null = null;
	export let onRetry: () => void;

	$: t = totals(rows);
	$: tm = topModel(rows);
	$: inactive = inactiveUsers(rows, users);
	$: modelBars = toBars(aggregateByModel(rows), 5);
	$: userBars = toBars(aggregateByUser(rows, users), 5);
	$: observationsDelta =
		t.observations > 0
			? `${formatNumber(t.observations)} ${$i18n.t('observations')}`
			: $i18n.t('no activity');
</script>

<div class="flex flex-col gap-3.5 font-['Inter']">
	<SectionHeader
		num="3"
		title={$i18n.t('Cost Analytics & Drill-Down')}
		subtitle={$i18n.t('PRD Feature 4 — by model, user, activity · period filters')}
	/>

	{#if loading}
		<div class="flex min-h-[300px] items-center justify-center">
			<Spinner className="size-5" />
		</div>
	{:else if failed}
		<div class="flex min-h-[300px] flex-col items-center justify-center gap-3 px-4 text-pii-muted">
			<span class="text-[13px]">{$i18n.t('Failed to load cost analytics.')}</span>
			{#if errorDetail}
				<span class="max-w-[520px] text-center text-[12px] break-words text-pii-muted opacity-80">
					{errorDetail}
				</span>
			{/if}
			<Button variant="primary" on:click={onRetry}>{$i18n.t('Retry')}</Button>
		</div>
	{:else if rows.length === 0}
		<div class="flex min-h-[300px] items-center justify-center text-[13px] text-pii-muted">
			{$i18n.t('No data found')}
		</div>
	{:else}
		<div class="flex flex-col gap-4">
			<!-- g3: KPI cards. Three abreast only once the viewport can hold them
			     with the admin sidebar open; below that they stack rather than
			     squeeze their labels out of the card. -->
			<div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
				<KpiCard
					type="numeric"
					label={$i18n.t('Total cost')}
					value={formatCostDisplay(t.cost)}
					delta={observationsDelta}
				/>
				<KpiCard
					type="text"
					label={$i18n.t('Top model by cost')}
					value={tm ? tm.name : $i18n.t('—')}
					delta={tm
						? `${formatCostDisplay(tm.cost)} · ${formatNumber(tm.tokens)} ${$i18n.t('tokens')}`
						: ''}
				/>
				<KpiCard
					type="numeric"
					label={$i18n.t('Inactive licensed users')}
					value={`${inactive.inactive} / ${inactive.total}`}
					pill={{ kind: 'info', label: $i18n.t('Reallocate?') }}
					delta={$i18n.t('zero usage in period')}
				/>
			</div>

			<!-- g2: bar cards. `min-w-0` so an over-long label truncates inside its
			     card instead of widening the grid track past the viewport. -->
			<div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
				<div
					class="flex min-w-0 flex-col gap-3.5 rounded-2xl border border-pii-line bg-pii-white p-5"
				>
					<span class="text-[13.5px] font-bold leading-[1.55] text-pii-ink"
						>{$i18n.t('Cost by model')}</span
					>
					<div class="flex flex-col gap-2">
						{#each modelBars as b}
							<BarRow
								color="blue"
								name={b.name}
								detail={b.detail}
								value={formatCostDisplay(b.cost)}
								percent={b.percent}
							/>
						{/each}
					</div>
				</div>
				<div
					class="flex min-w-0 flex-col gap-3.5 rounded-2xl border border-pii-line bg-pii-white p-5"
				>
					<span class="text-[13.5px] font-bold leading-[1.55] text-pii-ink"
						>{$i18n.t('Top users by cost')}</span
					>
					<div class="flex flex-col gap-2">
						{#each userBars as b}
							<BarRow
								color="green"
								name={b.name}
								detail={b.detail}
								value={formatCostDisplay(b.cost)}
								percent={b.percent}
							/>
						{/each}
					</div>
				</div>
			</div>
		</div>
	{/if}
</div>
