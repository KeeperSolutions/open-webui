<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';
	import {
		getBillingStatus,
		createCheckoutSession,
		getBillingPortalUrl,
		getTeamPortalUrl,
		getInvoices,
		getTopupOptions,
		createTeamTopup,
		createTeam,
		getTeamTiers,
		type BillingStatus,
		type Invoice,
		type TopupOption,
		type TeamTier
	} from '$lib/apis/billing';
	import TeamManagement from './TeamManagement.svelte';

	const i18n = getContext('i18n');

	let status: BillingStatus | null = null;
	let invoices: Invoice[] = [];
	let loading = true;
	let checkingOut = false;
	let openingPortal = false;
	let showTeamManagement = false;
	let showTopupModal = false;
	let topupOptions: TopupOption[] = [];
	let toppingUp = false;

	let showCreateTeam = false;
	let teamName = '';
	let selectedSeatCount: number | null = null;
	let creatingTeam = false;
	let teamTiers: TeamTier[] = [];

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
		const params = $page.url.searchParams;
		if (params.get('checkout') === 'success') {
			toast.success($i18n.t('Subscription activated! Welcome to the paid plan.'));
		} else if (params.get('checkout') === 'canceled') {
			toast.info($i18n.t('Checkout was canceled.'));
		} else if (params.get('topup') === 'success') {
			toast.success($i18n.t('Usage credits added to your team!'));
		} else if (params.get('topup') === 'canceled') {
			toast.info($i18n.t('Top-up was canceled.'));
		}

		try {
			[status, invoices] = await Promise.all([
				getBillingStatus(localStorage.token),
				getInvoices(localStorage.token)
			]);
			if (status?.plan_tier === 'team') {
				topupOptions = await getTopupOptions(localStorage.token).catch(() => []);
			}
			if (status?.plan_tier === 'trial' || status?.plan_tier === 'paid') {
				teamTiers = await getTeamTiers(localStorage.token).catch(() => []);
			}
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

	const handleTeamPortal = async () => {
		openingPortal = true;
		try {
			const { url } = await getTeamPortalUrl(localStorage.token);
			window.location.href = url;
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to open billing portal'));
			openingPortal = false;
		}
	};

	const handleTopup = async (amount_eur: number) => {
		toppingUp = true;
		try {
			const { url } = await createTeamTopup(localStorage.token, amount_eur);
			window.location.href = url;
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to start top-up'));
			toppingUp = false;
		}
	};

	const handleCreateTeam = async () => {
		if (!teamName.trim() || !selectedSeatCount) return;
		creatingTeam = true;
		try {
			const { url } = await createTeam(localStorage.token, teamName.trim(), selectedSeatCount);
			window.location.href = url;
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to create team'));
			creatingTeam = false;
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

			<div class="flex gap-2 flex-wrap">
				<button
					on:click={handleCheckout}
					disabled={checkingOut}
					class="px-4 py-2 rounded-lg text-sm bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition disabled:opacity-50"
				>
					{checkingOut ? $i18n.t('Redirecting...') : $i18n.t('Upgrade to €45/month')}
				</button>
				{#if teamTiers.length > 0}
					<button
						on:click={() => (showCreateTeam = true)}
						class="px-4 py-2 rounded-lg text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
					>
						{$i18n.t('Start a Team')}
					</button>
				{/if}
			</div>
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
				<div class="flex gap-2 flex-wrap">
					<button
						on:click={handlePortal}
						disabled={openingPortal}
						class="px-4 py-2 rounded-lg text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition disabled:opacity-50"
					>
						{openingPortal ? $i18n.t('Opening...') : $i18n.t('Manage payment method')}
					</button>
					{#if teamTiers.length > 0}
						<button
							on:click={() => (showCreateTeam = true)}
							class="px-4 py-2 rounded-lg text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
						>
							{$i18n.t('Start a Team')}
						</button>
					{/if}
				</div>
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

	{:else if status.plan_tier === 'team'}
		<!-- Team owner -->
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
			<div class="flex items-center justify-between flex-wrap gap-3">
				<div>
					<div class="font-medium">{status.team_name ?? $i18n.t('Team Plan')}</div>
					<div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
						{status.seat_limit} {$i18n.t('seats')}
					</div>
				</div>
				<div class="flex items-center gap-2">
					{#if status.subscription_status}
						<span class="text-xs px-2.5 py-1 rounded-full font-medium {STATUS_COLORS[status.subscription_status] ?? STATUS_COLORS.incomplete}">
							{$i18n.t(STATUS_LABELS[status.subscription_status] ?? status.subscription_status)}
						</span>
					{/if}
					<span class="text-xs px-2.5 py-1 rounded-full font-medium bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400">
						{$i18n.t('Team')}
					</span>
				</div>
			</div>

			<!-- Seat usage bar -->
			{#if status.seat_limit && status.seat_used !== null}
				<div class="space-y-1">
					<div class="flex justify-between text-xs text-gray-500 dark:text-gray-400">
						<span>{$i18n.t('Seats used')}</span>
						<span>{status.seat_used} / {status.seat_limit}</span>
					</div>
					<div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
						<div
							class="h-2 rounded-full bg-orange-500 transition-all"
							style="width: {Math.min(100, ((status.seat_used ?? 0) / (status.seat_limit ?? 1)) * 100)}%"
						></div>
					</div>
				</div>
			{/if}

			<!-- Usage budget bar -->
			{#if status.usage_budget_eur}
				{@const used = status.team_month_cost_eur ?? 0}
				{@const total = (status.usage_budget_eur ?? 0) + (status.extra_credit_eur ?? 0)}
				{@const usedPct = total > 0 ? Math.min(100, (used / total) * 100) : 0}
				<div class="space-y-1.5">
					<div class="flex justify-between text-xs text-gray-500 dark:text-gray-400">
						<span>{$i18n.t('Usage this month')}</span>
						<span>€{used.toFixed(4)} / €{total.toFixed(2)}</span>
					</div>
					<div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
						<div
							class="h-2 rounded-full transition-all {usedPct >= 90 ? 'bg-red-500' : usedPct >= 70 ? 'bg-yellow-500' : 'bg-orange-500'}"
							style="width: {usedPct}%"
						></div>
					</div>
					<div class="text-xs font-medium {(status.usage_budget_remaining_eur ?? 1) <= 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-600 dark:text-gray-400'}">
						{#if (status.usage_budget_remaining_eur ?? 1) <= 0}
							{$i18n.t('Budget exhausted — buy more usage to continue.')}
						{:else}
							€{(status.usage_budget_remaining_eur ?? 0).toFixed(4)} {$i18n.t('remaining')}
						{/if}
					</div>
					{#if status.extra_credit_eur}
						<div class="text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t('Includes €{{extra}} in purchased credits', { extra: status.extra_credit_eur.toFixed(2) })}
						</div>
					{/if}
				</div>

				{#if (status.usage_budget_remaining_eur ?? 1) <= 0}
					<div class="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
						{$i18n.t("Your team's usage budget is exhausted. Buy more credits to restore access.")}
					</div>
				{/if}
			{:else}
				<div class="grid grid-cols-2 gap-4 text-sm">
					<div class="space-y-1">
						<div class="text-gray-500 dark:text-gray-400 text-xs">{$i18n.t('Team usage this month')}</div>
						<div class="font-semibold text-lg">€{(status.team_month_cost_eur ?? 0).toFixed(4)}</div>
					</div>
					{#if status.upcoming_invoice_eur !== null}
						<div class="space-y-1">
							<div class="text-gray-500 dark:text-gray-400 text-xs">{$i18n.t('Upcoming invoice')}</div>
							<div class="font-semibold text-lg">€{status.upcoming_invoice_eur?.toFixed(2)}</div>
						</div>
					{/if}
				</div>
			{/if}

			{#if status.upcoming_invoice_eur !== null && status.usage_budget_eur}
				<div class="text-sm">
					<div class="text-gray-500 dark:text-gray-400 text-xs">{$i18n.t('Upcoming invoice')}</div>
					<div class="font-semibold">€{status.upcoming_invoice_eur?.toFixed(2)}</div>
				</div>
			{/if}

			{#if status.subscription_status === 'past_due'}
				<div class="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
					{$i18n.t('Your last payment failed. Please update your payment method to avoid service interruption for your team.')}
				</div>
			{/if}

			<div class="flex gap-2 flex-wrap">
				<button
					on:click={() => (showTeamManagement = true)}
					class="px-4 py-2 rounded-lg text-sm bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition"
				>
					{$i18n.t('Manage Team')}
				</button>
				{#if topupOptions.length > 0}
					<button
						on:click={() => (showTopupModal = true)}
						class="px-4 py-2 rounded-lg text-sm bg-orange-600 text-white hover:opacity-80 transition"
					>
						{$i18n.t('Buy more usage')}
					</button>
				{/if}
				<button
					on:click={handleTeamPortal}
					disabled={openingPortal}
					class="px-4 py-2 rounded-lg text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition disabled:opacity-50"
				>
					{openingPortal ? $i18n.t('Opening...') : $i18n.t('Manage billing')}
				</button>
			</div>
		</div>

		<!-- Invoice history for team owner -->
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
											<a href={inv.pdf_url} target="_blank" rel="noreferrer" class="text-xs text-blue-600 dark:text-blue-400 hover:underline">
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

	{:else if status.plan_tier === 'team_member'}
		<!-- Team member -->
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
			<div class="flex items-center justify-between flex-wrap gap-3">
				<div>
					<div class="font-medium">{status.team_name ?? $i18n.t('Team Plan')}</div>
					{#if status.team_owner_name}
						<div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
							{$i18n.t('Managed by {{owner}}', { owner: status.team_owner_name })}
						</div>
					{/if}
				</div>
				<span class="text-xs px-2.5 py-1 rounded-full font-medium bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400">
					{$i18n.t('Team')}
				</span>
			</div>

			{#if status.subscription_status === 'past_due' || status.subscription_status === 'canceled'}
				<div class="rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 px-4 py-3 text-sm text-yellow-700 dark:text-yellow-400">
					{$i18n.t('Your team subscription has an issue. Please contact your team owner.')}
				</div>
			{/if}

			<!-- Team budget bar (member view) -->
			{#if status.usage_budget_eur}
				{@const total = (status.usage_budget_eur ?? 0) + (status.extra_credit_eur ?? 0)}
				{@const usedPct = total > 0 ? Math.min(100, (1 - (status.usage_budget_remaining_eur ?? 0) / total) * 100) : 0}
				<div class="space-y-1.5">
					<div class="flex justify-between text-xs text-gray-500 dark:text-gray-400">
						<span>{$i18n.t('Team budget remaining')}</span>
						<span>€{(status.usage_budget_remaining_eur ?? 0).toFixed(2)} / €{total.toFixed(2)}</span>
					</div>
					<div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
						<div
							class="h-2 rounded-full transition-all {usedPct >= 90 ? 'bg-red-500' : usedPct >= 70 ? 'bg-yellow-500' : 'bg-orange-500'}"
							style="width: {usedPct}%"
						></div>
					</div>
				</div>
			{/if}

			<div class="space-y-1">
				<div class="text-gray-500 dark:text-gray-400 text-xs">{$i18n.t('Your usage this month')}</div>
				<div class="font-semibold text-lg">€{status.current_month_cost_eur.toFixed(4)}</div>
			</div>
		</div>

	{:else}
		<!-- No billing record yet (unconfigured) -->
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 text-sm text-gray-500 dark:text-gray-400">
			{$i18n.t('Your billing account is being set up. Please refresh the page in a moment.')}
		</div>
	{/if}
</div>

{#if showTeamManagement}
	<TeamManagement onClose={() => (showTeamManagement = false)} />
{/if}

{#if showCreateTeam}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
		on:click|self={() => (showCreateTeam = false)}
		role="dialog"
		aria-modal="true"
	>
		<div class="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 space-y-5">
			<div class="flex items-center justify-between">
				<h2 class="font-semibold text-lg">{$i18n.t('Start a Team')}</h2>
				<button
					on:click={() => (showCreateTeam = false)}
					class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
					aria-label="Close"
				>
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			<!-- Team name -->
			<div class="space-y-1.5">
				<label class="text-sm font-medium" for="team-name">{$i18n.t('Team name')}</label>
				<input
					id="team-name"
					type="text"
					bind:value={teamName}
					placeholder={$i18n.t('e.g. Acme Corp')}
					class="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-transparent px-3 py-2 focus:outline-none focus:ring-2 focus:ring-black dark:focus:ring-white"
				/>
			</div>

			<!-- Seat tier selection -->
			<div class="space-y-1.5">
				<div class="text-sm font-medium">{$i18n.t('Seat plan')}</div>
				<div class="space-y-2">
					{#each [...teamTiers].sort((a, b) => a.seat_count - b.seat_count) as tier}
						<button
							on:click={() => (selectedSeatCount = tier.seat_count)}
							class="w-full flex items-center justify-between px-4 py-3 rounded-xl border-2 transition
								{selectedSeatCount === tier.seat_count
									? 'border-black dark:border-white bg-gray-50 dark:bg-gray-800'
									: 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800'}"
						>
							<div class="text-left">
								<div class="font-medium text-sm">{tier.seat_count} {$i18n.t('seats')}</div>
							</div>
							<div class="text-sm font-semibold">€{tier.price_eur.toFixed(0)}/mo</div>
						</button>
					{/each}
				</div>
			</div>

			<button
				on:click={handleCreateTeam}
				disabled={creatingTeam || !teamName.trim() || !selectedSeatCount}
				class="w-full px-4 py-2.5 rounded-lg text-sm bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition disabled:opacity-50"
			>
				{creatingTeam ? $i18n.t('Redirecting to checkout...') : $i18n.t('Continue to checkout')}
			</button>
		</div>
	</div>
{/if}

{#if showTopupModal}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
		on:click|self={() => (showTopupModal = false)}
		role="dialog"
		aria-modal="true"
	>
		<div class="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 space-y-4">
			<div class="flex items-center justify-between">
				<h2 class="font-semibold text-lg">{$i18n.t('Buy more usage')}</h2>
				<button
					on:click={() => (showTopupModal = false)}
					class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
					aria-label="Close"
				>
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>
			<p class="text-sm text-gray-500 dark:text-gray-400">
				{$i18n.t('Select a credit amount to add to your team. Credits are valid until the end of the current month.')}
			</p>
			<div class="space-y-2">
				{#each topupOptions as opt}
					<button
						on:click={() => handleTopup(opt.amount_eur)}
						disabled={toppingUp}
						class="w-full flex items-center justify-between px-4 py-3 rounded-xl border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 transition disabled:opacity-50"
					>
						<span class="font-medium">€{opt.amount_eur.toFixed(0)} {$i18n.t('credits')}</span>
						<span class="text-sm text-gray-500 dark:text-gray-400">€{opt.amount_eur.toFixed(2)}</span>
					</button>
				{/each}
			</div>
			{#if toppingUp}
				<div class="text-center text-sm text-gray-500 dark:text-gray-400 animate-pulse">{$i18n.t('Redirecting to checkout...')}</div>
			{/if}
		</div>
	</div>
{/if}
