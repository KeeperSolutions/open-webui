<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { getLangfuseMetrics, type MetricRow } from '$lib/apis/langfuse';
	import {
		sortRows,
		totalPages as calcTotalPages,
		paginateRows,
		totalTokens,
		totalCost,
		formatCost,
		buildCsvLines,
		rowNumber,
		type SortKey
	} from '$lib/apis/langfuse/tableUtils';
	import type { i18n as i18nType } from 'i18next';

	const i18n: Writable<i18nType> = getContext('i18n');

	const PERIODS = [
		{ value: 'today', label: 'Today' },
		{ value: 'day', label: 'Yesterday' },
		{ value: 'week', label: 'Last Week' },
		{ value: 'month', label: 'Last Month' },
		{ value: 'custom', label: 'Custom Days' }
	];

	let period = 'today';
	let customDays = 7;
	let rows: MetricRow[] = [];
	let loading = false;

	let pageSize = 25;
	let currentPage = 1;

	let sortKey: SortKey = 'tokens';
	let sortAsc = false;

	$: sortedRows = sortRows(rows, sortKey, sortAsc);
	$: numPages = calcTotalPages(sortedRows.length, pageSize);
	$: pagedRows = paginateRows(sortedRows, currentPage, pageSize);

	function setSort(key: SortKey) {
		if (sortKey === key) {
			sortAsc = !sortAsc;
		} else {
			sortKey = key;
			sortAsc = key === 'user' || key === 'model';
		}
		currentPage = 1;
	}

	const loadMetrics = async (p: string = period) => {
		loading = true;
		currentPage = 1;
		try {
			rows = await getLangfuseMetrics(
				localStorage.token,
				p,
				p === 'custom' ? customDays : undefined
			);
		} catch (e) {
			toast.error(String(e) || 'Failed to fetch Langfuse metrics');
			rows = [];
		} finally {
			loading = false;
		}
	};

	onMount(() => {
		loadMetrics(period);
	});

	function exportCsv() {
		const lines = buildCsvLines(sortedRows);
		const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `langfuse-metrics-${period}-${new Date().toISOString().slice(0, 10)}.csv`;
		a.click();
		URL.revokeObjectURL(url);
	}
</script>

<div class="w-full px-4 py-6 space-y-4">
	<div class="flex items-center justify-between">
		<h2 class="text-xl font-semibold">{$i18n.t('Langfuse Metrics')}</h2>
		{#if rows.length > 0}
			<button
				on:click={exportCsv}
				class="px-3 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
			>
				{$i18n.t('Export CSV')}
			</button>
		{/if}
	</div>

	<div class="flex flex-wrap items-end gap-3">
		<div class="flex flex-col gap-1">
			<label for="period-select" class="text-xs text-gray-500 dark:text-gray-400"
				>{$i18n.t('Period')}</label
			>
			<select
				id="period-select"
				bind:value={period}
				on:change={() => {
					if (period !== 'custom') loadMetrics(period);
				}}
				class="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 pl-3 pr-8 py-1.5"
			>
				{#each PERIODS as p}
					<option value={p.value}>{$i18n.t(p.label)}</option>
				{/each}
			</select>
		</div>

		{#if period === 'custom'}
			<div class="flex flex-col gap-1">
				<label for="custom-days" class="text-xs text-gray-500 dark:text-gray-400"
					>{$i18n.t('Days')}</label
				>
				<input
					id="custom-days"
					type="text"
					inputmode="numeric"
					bind:value={customDays}
					on:input={(e) => {
						const digits = e.currentTarget.value.replace(/\D/g, '');
						customDays = digits ? parseInt(digits, 10) : 1;
						e.currentTarget.value = String(customDays);
					}}
					on:change={() => loadMetrics(period)}
					class="w-24 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1.5"
				/>
			</div>
		{/if}

		<button
			on:click={() => loadMetrics(period)}
			disabled={loading}
			class="px-4 py-1.5 rounded-lg text-sm bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition disabled:opacity-50"
		>
			{loading ? $i18n.t('Loading...') : $i18n.t('Refresh')}
		</button>

		<div class="flex flex-col gap-1 ml-auto">
			<label for="page-size" class="text-xs text-gray-500 dark:text-gray-400"
				>{$i18n.t('Rows per page')}</label
			>
			<select
				id="page-size"
				bind:value={pageSize}
				on:change={() => (currentPage = 1)}
				class="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 pl-3 pr-8 py-1.5"
			>
				<option value={10}>10</option>
				<option value={25}>25</option>
				<option value={50}>50</option>
				<option value={0}>{$i18n.t('All')}</option>
			</select>
		</div>
	</div>

	{#if loading}
		<div class="text-sm text-gray-500 dark:text-gray-400">{$i18n.t('Loading...')}</div>
	{:else if rows.length === 0}
		<div class="text-sm text-gray-500 dark:text-gray-400">
			{$i18n.t('No data for the selected period.')}
		</div>
	{:else}
		<div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
			<table class="w-full text-sm">
				<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
					<tr>
						<th class="px-4 py-2 font-medium text-left w-16 text-gray-400 dark:text-gray-500">#</th>
						{#each [['user', 'User', 'left'], ['model', 'Model', 'left'], ['tokens', 'Tokens', 'right'], ['cost', 'Cost', 'right']] as const as [key, label, align]}
							<th
								class="px-4 py-2 font-medium cursor-pointer select-none text-{align} hover:text-gray-700 dark:hover:text-gray-200"
								on:click={() => setSort(key)}
							>
								{$i18n.t(label)}
								{#if sortKey === key}{sortAsc ? '↑' : '↓'}{/if}
							</th>
						{/each}
					</tr>
				</thead>
				<tbody class="divide-y divide-gray-100 dark:divide-gray-800">
					{#each pagedRows as row, i}
						<tr class="hover:bg-gray-50 dark:hover:bg-gray-850 transition">
							<td class="px-4 py-2 text-xs text-gray-400 dark:text-gray-500 text-left"
								>{rowNumber(currentPage, pageSize, sortedRows.length, i)}</td
							>
							<td class="px-4 py-2 font-mono text-xs">{row.user}</td>
							<td class="px-4 py-2 text-gray-600 dark:text-gray-300">{row.model}</td>
							<td class="px-4 py-2 text-right">{row.tokens.toLocaleString()}</td>
							<td class="px-4 py-2 text-right">{formatCost(row.cost)}</td>
						</tr>
					{/each}
				</tbody>
				<tfoot class="bg-gray-50 dark:bg-gray-850 font-medium">
					<tr>
						<td class="px-4 py-2 text-gray-500 dark:text-gray-400" colspan="3"
							>{$i18n.t('Total')}</td
						>
						<td class="px-4 py-2 text-right">{totalTokens(rows).toLocaleString()}</td>
						<td class="px-4 py-2 text-right">{formatCost(totalCost(rows))}</td>
					</tr>
				</tfoot>
			</table>
		</div>

		{#if numPages > 1}
			<div class="flex items-center justify-between text-sm">
				<span class="text-gray-500 dark:text-gray-400">
					{$i18n.t('Page')}
					{currentPage} / {numPages}
				</span>
				<div class="flex gap-2">
					<button
						on:click={() => (currentPage -= 1)}
						disabled={currentPage === 1}
						class="px-3 py-1 rounded-lg border border-gray-200 dark:border-gray-700 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
					>
						&larr;
					</button>
					<button
						on:click={() => (currentPage += 1)}
						disabled={currentPage === numPages}
						class="px-3 py-1 rounded-lg border border-gray-200 dark:border-gray-700 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
					>
						&rarr;
					</button>
				</div>
			</div>
		{/if}
	{/if}
</div>
