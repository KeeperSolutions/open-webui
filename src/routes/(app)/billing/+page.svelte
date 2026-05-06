<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';
	import {
		getBillingStatus,
		createCheckoutSession,
		getBillingPortalUrl,
		getInvoices,
		type BillingStatus,
		type Invoice
	} from '$lib/apis/billing';

	const i18n = getContext('i18n');

	let status: BillingStatus | null = null;
	let invoices: Invoice[] = [];
	let loading = true;
	let checkingOut = false;
	let openingPortal = false;

	const STATUS_LABELS: Record<string, string> = {
		active: 'Active',
		past_due: 'Past Due',
		canceled: 'Canceled',
		incomplete: 'Incomplete',
		trialing: 'Trial'
	};

	const STATUS_COLORS: Record<string, string> = {
		active: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
		past_due: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
		canceled: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
		incomplete: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
		trialing: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
	};

	onMount(async () => {
		// Show success/cancel toast from Stripe redirect
		const params = $page.url.searchParams;
		if (params.get('checkout') === 'success') {
			toast.success($i18n.t('Subscription activated! Welcome to the paid plan.'));
		} else if (params.get('checkout') === 'canceled') {
			toast.info($i18n.t('Checkout was canceled.'));
		}

		try {
			[status, invoices] = await Promise.all([
				getBillingStatus(localStorage.token),
				getInvoices(localStorage.token)
			]);
		} catch (e: any) {
			toast.error(e?.message ?? 'Failed to load billing info');
		} finally {
			loading = false;
		}
	});

	const handleCheckout = async () => {
		checkingOut = true;
		try {
			const { url } = await createCheckoutSession(localStorage.token);
			window.location.href = url;
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to start checkout'));
			checkingOut = false;
		}
	};

	const handlePortal = async () => {
		openingPortal = true;
		try {
			const { url } = await getBillingPortalUrl(localStorage.token);
			window.location.href = url;
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to open billing portal'));
			openingPortal = false;
		}
	};

	const formatDate = (ts: number) =>
		new Date(ts * 1000).toLocaleDateString('default', {
			year: 'numeric',
			month: 'long',
			day: 'numeric'
		});

	$: creditPct =
		status?.plan_tier === 'trial' && status.credit_limit_eur > 0
			? Math.min(100, (status.credit_used_eur / status.credit_limit_eur) * 100)
			: 0;
</script>

<div class="w-full max-w-3xl mx-auto px-4 py-8 space-y-6">
	<h1 class="text-2xl font-semibold">{$i18n.t('Billing')}</h1>

	{#if loading}
		<div class="text-sm text-gray-500 dark:text-gray-400 animate-pulse">{$i18n.t('Loading...')}</div>

	{:else if !status?.enabled}
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 text-sm text-gray-500 dark:text-gray-400">
			{$i18n.t('Billing is not enabled on this instance.')}
		</div>

	{:else if status.plan_tier === 'internal'}
		<!-- Internal user -->
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
			<div class="flex items-center gap-3">
				<div class="font-medium">{$i18n.t('Internal Plan')}</div>
				<span class="text-xs px-2.5 py-1 rounded-full font-medium bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">
					{$i18n.t('Internal')}
				</span>
			</div>
			<p class="text-sm text-gray-500 dark:text-gray-400">
				{$i18n.t('You are on the internal plan. Usage is tracked but not billed.')}
			</p>
			<div class="space-y-1">
				<div class="text-gray-500 dark:text-gray-400 text-xs">{$i18n.t('This month usage')}</div>
				<div class="font-semibold text-lg">€{status.current_month_cost_eur.toFixed(4)}</div>
			</div>
		</div>

	{:else if status.plan_tier === 'trial'}
		<!-- Trial user -->
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
			<div class="flex items-center justify-between flex-wrap gap-3">
				<div class="font-medium">{$i18n.t('Trial Plan')}</div>
				<span class="text-xs px-2.5 py-1 rounded-full font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
					{$i18n.t('Trial')}
				</span>
			</div>

			<p class="text-sm text-gray-500 dark:text-gray-400">
				{$i18n.t('You have a €{{limit}} trial credit. Upgrade to continue after it runs out.', { limit: status.credit_limit_eur.toFixed(2) })}
			</p>

			<!-- Credit progress bar -->
			<div class="space-y-1.5">
				<div class="flex justify-between text-xs text-gray-500 dark:text-gray-400">
					<span>{$i18n.t('Credit used')}</span>
					<span>€{status.credit_used_eur.toFixed(4)} / €{status.credit_limit_eur.toFixed(2)}</span>
				</div>
				<div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
					<div
						class="h-2 rounded-full transition-all {creditPct >= 90 ? 'bg-red-500' : creditPct >= 60 ? 'bg-yellow-500' : 'bg-blue-500'}"
						style="width: {creditPct}%"
					></div>
				</div>
				<div class="text-xs font-medium {status.credit_remaining_eur <= 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-600 dark:text-gray-400'}">
					{#if status.credit_remaining_eur <= 0}
						{$i18n.t('Credit exhausted — upgrade to continue.')}
					{:else}
						€{status.credit_remaining_eur.toFixed(4)} {$i18n.t('remaining')}
					{/if}
				</div>
			</div>

			{#if status.credit_remaining_eur <= 0}
				<div class="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
					{$i18n.t('Your trial credit is used up. Upgrade to keep using the service.')}
				</div>
			{/if}

			<button
				on:click={handleCheckout}
				disabled={checkingOut}
				class="px-4 py-2 rounded-lg text-sm bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition disabled:opacity-50"
			>
				{checkingOut ? $i18n.t('Redirecting...') : $i18n.t('Upgrade to €45/month')}
			</button>
		</div>

	{:else if status.plan_tier === 'paid'}
		<!-- Paid subscriber -->
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
			<div class="flex items-center justify-between flex-wrap gap-3">
				<div class="font-medium">{$i18n.t('Subscription')}</div>
				{#if status.subscription_status}
					<span class="text-xs px-2.5 py-1 rounded-full font-medium {STATUS_COLORS[status.subscription_status] ?? STATUS_COLORS.incomplete}">
						{$i18n.t(STATUS_LABELS[status.subscription_status] ?? status.subscription_status)}
					</span>
				{/if}
			</div>

			<div class="grid grid-cols-2 gap-4 text-sm">
				<div class="space-y-1">
					<div class="text-gray-500 dark:text-gray-400 text-xs">{$i18n.t('This month usage')}</div>
					<div class="font-semibold text-lg">€{status.current_month_cost_eur.toFixed(4)}</div>
				</div>
				{#if status.upcoming_invoice_eur !== null}
					<div class="space-y-1">
						<div class="text-gray-500 dark:text-gray-400 text-xs">{$i18n.t('Upcoming invoice')}</div>
						<div class="font-semibold text-lg">€{status.upcoming_invoice_eur?.toFixed(2)}</div>
					</div>
				{/if}
			</div>

			{#if status.subscription_status === 'past_due'}
				<div class="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
					{$i18n.t('Your last payment failed. Please update your payment method to avoid service interruption.')}
				</div>
			{/if}

			{#if status.subscription_status === 'canceled'}
				<div class="rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
					{$i18n.t('Your subscription has been canceled.')}
				</div>
				<button
					on:click={handleCheckout}
					disabled={checkingOut}
					class="px-4 py-2 rounded-lg text-sm bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition disabled:opacity-50"
				>
					{checkingOut ? $i18n.t('Redirecting...') : $i18n.t('Resubscribe')}
				</button>
			{:else}
				<button
					on:click={handlePortal}
					disabled={openingPortal}
					class="px-4 py-2 rounded-lg text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition disabled:opacity-50"
				>
					{openingPortal ? $i18n.t('Opening...') : $i18n.t('Manage payment method')}
				</button>
			{/if}
		</div>

		<!-- Invoice history -->
		{#if invoices.length > 0}
			<div class="space-y-2">
				<div class="font-medium text-sm">{$i18n.t('Invoice history')}</div>
				<div class="rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
					<table class="w-full text-sm">
						<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
							<tr>
								<th class="text-left px-4 py-2 font-medium">{$i18n.t('Date')}</th>
								<th class="text-right px-4 py-2 font-medium">{$i18n.t('Amount')}</th>
								<th class="text-left px-4 py-2 font-medium">{$i18n.t('Status')}</th>
								<th class="px-4 py-2"></th>
							</tr>
						</thead>
						<tbody class="divide-y divide-gray-100 dark:divide-gray-800">
							{#each invoices as inv}
								<tr class="hover:bg-gray-50 dark:hover:bg-gray-850 transition">
									<td class="px-4 py-2">{formatDate(inv.date)}</td>
									<td class="px-4 py-2 text-right font-medium">€{inv.amount_eur.toFixed(2)}</td>
									<td class="px-4 py-2">
										<span class="text-xs px-2 py-0.5 rounded-full {STATUS_COLORS[inv.status] ?? STATUS_COLORS.incomplete}">
											{$i18n.t(STATUS_LABELS[inv.status] ?? inv.status)}
										</span>
									</td>
									<td class="px-4 py-2 text-right">
										{#if inv.pdf_url}
											<a
												href={inv.pdf_url}
												target="_blank"
												rel="noreferrer"
												class="text-xs text-blue-600 dark:text-blue-400 hover:underline"
											>
												{$i18n.t('PDF')}
											</a>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		{/if}

	{:else}
		<!-- No billing record yet (unconfigured) -->
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 text-sm text-gray-500 dark:text-gray-400">
			{$i18n.t('Your billing account is being set up. Please refresh the page in a moment.')}
		</div>
	{/if}
</div>
