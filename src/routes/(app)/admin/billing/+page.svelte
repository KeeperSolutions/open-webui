<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getAdminBillingSummary, type AdminBillingRow } from '$lib/apis/billing';
	import { PLAN_TIER } from '$lib/billing/planTiers';

	const i18n = getContext('i18n');

	let rows: AdminBillingRow[] = [];
	let loading = true;

	const STATUS_COLORS: Record<string, string> = {
		active: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
		past_due: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
		canceled: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
		incomplete: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
	};

	const TIER_COLORS: Record<string, string> = {
		internal: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
		trial: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
		paid: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
	};

	onMount(async () => {
		if (!localStorage.token) {
			loading = false;
			return;
		}
		try {
			rows = await getAdminBillingSummary(localStorage.token);
		} catch (e: any) {
			toast.error($i18n.t('Failed to load billing summary'));
			console.error('[billing admin]', e);
		} finally {
			loading = false;
		}
	});

	$: totalCost = rows.reduce((s, r) => s + r.current_month_cost_eur, 0);
	$: paidCount = rows.filter((r) => (r.plan_tier === PLAN_TIER.PRO || r.plan_tier === PLAN_TIER.PREMIUM) && r.subscription_status === 'active').length;
	$: trialCount = rows.filter((r) => r.plan_tier === PLAN_TIER.TRIAL).length;
	$: pastDueCount = rows.filter((r) => r.subscription_status === 'past_due').length;
</script>

<div class="w-full px-4 py-6 space-y-6">
	<h2 class="text-xl font-semibold">{$i18n.t('Billing Overview')}</h2>

	{#if loading}
		<div class="text-sm text-gray-500 dark:text-gray-400 animate-pulse">{$i18n.t('Loading...')}</div>
	{:else}
		<!-- Summary cards -->
		<div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
			<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-4 space-y-1">
				<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Total users')}</div>
				<div class="text-2xl font-semibold">{rows.length}</div>
			</div>
			<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-4 space-y-1">
				<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Paid (active)')}</div>
				<div class="text-2xl font-semibold text-green-600 dark:text-green-400">{paidCount}</div>
			</div>
			<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-4 space-y-1">
				<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Trial')}</div>
				<div class="text-2xl font-semibold text-blue-600 dark:text-blue-400">{trialCount}</div>
			</div>
			<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-4 space-y-1">
				<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('This month total')}</div>
				<div class="text-2xl font-semibold">€{totalCost.toFixed(2)}</div>
			</div>
		</div>

		<!-- User table -->
		{#if rows.length === 0}
			<div class="text-sm text-gray-500 dark:text-gray-400">
				{$i18n.t('No billing records found.')}
			</div>
		{:else}
			<div class="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700">
				<table class="w-full text-sm">
					<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
						<tr>
							<th class="text-left px-4 py-2.5 font-medium">{$i18n.t('User')}</th>
							<th class="text-left px-4 py-2.5 font-medium">{$i18n.t('Email')}</th>
							<th class="text-left px-4 py-2.5 font-medium">{$i18n.t('Plan')}</th>
							<th class="text-left px-4 py-2.5 font-medium">{$i18n.t('Sub. Status')}</th>
							<th class="text-right px-4 py-2.5 font-medium">{$i18n.t('This month')}</th>
							<th class="text-left px-4 py-2.5 font-medium">{$i18n.t('Stripe')}</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-gray-100 dark:divide-gray-800">
						{#each rows as row}
							<tr class="hover:bg-gray-50 dark:hover:bg-gray-850 transition">
								<td class="px-4 py-2.5 font-medium truncate max-w-[140px]">{row.name}</td>
								<td class="px-4 py-2.5 text-gray-500 dark:text-gray-400 font-mono text-xs">{row.email}</td>
								<td class="px-4 py-2.5">
									{#if row.plan_tier}
										<span class="text-xs px-2 py-0.5 rounded-full font-medium {TIER_COLORS[row.plan_tier] ?? ''}">
											{$i18n.t(row.plan_tier)}
										</span>
									{:else}
										<span class="text-xs text-gray-400 dark:text-gray-600">—</span>
									{/if}
								</td>
								<td class="px-4 py-2.5">
									{#if row.subscription_status}
										<span class="text-xs px-2 py-0.5 rounded-full font-medium {STATUS_COLORS[row.subscription_status] ?? STATUS_COLORS.incomplete}">
											{$i18n.t(row.subscription_status)}
										</span>
									{:else}
										<span class="text-xs text-gray-400 dark:text-gray-600">—</span>
									{/if}
								</td>
								<td class="px-4 py-2.5 text-right font-medium">
									€{row.current_month_cost_eur.toFixed(4)}
								</td>
								<td class="px-4 py-2.5">
									{#if row.stripe_customer_id}
										<span class="font-mono text-xs text-gray-400 dark:text-gray-500 truncate block max-w-[120px]">
											{row.stripe_customer_id}
										</span>
									{:else}
										<span class="text-xs text-gray-400 dark:text-gray-600">—</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
					<tfoot class="bg-gray-50 dark:bg-gray-850 font-medium text-sm">
						<tr>
							<td class="px-4 py-2.5 text-gray-500 dark:text-gray-400" colspan="4">{$i18n.t('Total')}</td>
							<td class="px-4 py-2.5 text-right">€{totalCost.toFixed(4)}</td>
							<td></td>
						</tr>
					</tfoot>
				</table>
			</div>
		{/if}
	{/if}
</div>
