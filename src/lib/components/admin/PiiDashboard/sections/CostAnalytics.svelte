<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import SectionHeader from '../parts/SectionHeader.svelte';
	import KpiCard from '../parts/KpiCard.svelte';
	import BarRow from '../parts/BarRow.svelte';
	import Button from '../parts/Button.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { getLangfuseMetrics, type MetricRow } from '$lib/apis/langfuse';
	import { formatCost } from '$lib/apis/langfuse/tableUtils';
	import { getAllUsers } from '$lib/apis/users';
	import { formatNumber } from '$lib/utils';
	import { toLangfuseParams, type PeriodKey } from '../periods';
	import {
		aggregateByModel,
		aggregateByUser,
		toBars,
		topModel,
		totals,
		inactiveUsers
	} from './costAnalytics';

	const i18n: Writable<i18nType> = getContext('i18n');

	export let period: PeriodKey = 'week';
	export let customDays = 7;
	/** Reported upward so the topbar can name the window next to the control
	 *  that selects it. Bound, not derived — only the backend knows the window. */
	export let windowFrom = '';
	export let windowTo = '';

	/** Bound upward so the topbar can mark the window it shows as out of date
	 *  while a newer one is being fetched. */
	export let loading = true;

	let loadError: string | null = null;
	let rows: MetricRow[] = [];
	let allUsers: { id: string; email: string }[] = [];
	let inFlight: AbortController | null = null;

	const load = async () => {
		// A newer period selection supersedes whatever is still in flight: abort it
		// so its (possibly slower) response cannot overwrite the fresher state.
		inFlight?.abort();
		const controller = new AbortController();
		inFlight = controller;

		loading = true;
		loadError = null;
		try {
			const { period: p, days } = toLangfuseParams(period, customDays);
			const res = await getLangfuseMetrics(localStorage.token, p, days, controller.signal);
			const usersRes = await getAllUsers(localStorage.token);
			if (controller.signal.aborted) return;
			rows = res.rows;
			windowFrom = res.from;
			windowTo = res.to;
			allUsers = usersRes?.users ?? [];
		} catch (e) {
			if (controller.signal.aborted) return;
			loadError = String((e as any)?.detail ?? e ?? 'Failed to load');
			rows = [];
			// Drop the window too: it belongs to the previous period, and the topbar
			// renders it at full opacity — i.e. as authoritative — once loading ends.
			windowFrom = '';
			windowTo = '';
		} finally {
			// Superseded loads leave `loading` alone — the newer one owns it.
			if (!controller.signal.aborted) loading = false;
		}
	};

	// Reload whenever the period selection changes.
	$: period, customDays, load();

	$: t = totals(rows);
	$: tm = topModel(rows);
	$: inactive = inactiveUsers(rows, allUsers);
	$: modelBars = toBars(aggregateByModel(rows), 5);
	$: userBars = toBars(aggregateByUser(rows), 5);
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
	{:else if loadError}
		<div class="flex min-h-[300px] flex-col items-center justify-center gap-3 text-pii-muted">
			<span class="text-[13px]">{$i18n.t('Failed to load cost analytics.')}</span>
			<Button variant="primary" on:click={load}>{$i18n.t('Retry')}</Button>
		</div>
	{:else if rows.length === 0}
		<div class="flex min-h-[300px] items-center justify-center text-[13px] text-pii-muted">
			{$i18n.t('No data found')}
		</div>
	{:else}
		<div class="flex flex-col gap-4">
			<!-- g3: KPI cards -->
			<div class="flex gap-4">
				<KpiCard
					type="numeric"
					label={$i18n.t('Total cost')}
					value={formatCost(t.cost, 2)}
					delta={observationsDelta}
				/>
				<KpiCard
					type="text"
					label={$i18n.t('Top model by cost')}
					value={tm ? tm.name : $i18n.t('—')}
					delta={tm ? `${formatCost(tm.cost, 2)} · ${formatNumber(tm.tokens)} ${$i18n.t('tokens')}` : ''}
				/>
				<KpiCard
					type="numeric"
					label={$i18n.t('Inactive licensed users')}
					value={`${inactive.inactive} / ${inactive.total}`}
					pill={{ kind: 'info', label: $i18n.t('Reallocate?') }}
					delta={$i18n.t('zero usage in period')}
				/>
			</div>

			<!-- g2: bar cards -->
			<div class="flex gap-4">
				<div class="flex flex-1 flex-col gap-3.5 rounded-2xl border border-pii-line bg-pii-white p-5">
					<span class="text-[13.5px] font-bold leading-[1.55] text-pii-ink">{$i18n.t('Cost by model')}</span>
					<div class="flex flex-col gap-2">
						{#each modelBars as b}
							<BarRow color="blue" name={b.name} value={formatCost(b.cost, 3)} percent={b.percent} />
						{/each}
					</div>
				</div>
				<div class="flex flex-1 flex-col gap-3.5 rounded-2xl border border-pii-line bg-pii-white p-5">
					<span class="text-[13.5px] font-bold leading-[1.55] text-pii-ink">{$i18n.t('Top users by cost')}</span>
					<div class="flex flex-col gap-2">
						{#each userBars as b}
							<BarRow color="green" name={b.name} value={formatCost(b.cost, 3)} percent={b.percent} />
						{/each}
					</div>
				</div>
			</div>
		</div>
	{/if}
</div>
