<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';
	import { showSidebar } from '$lib/stores';
	import {
		getBillingStatus,
		createCheckoutSession,
		getBillingPortalUrl,
		getTeamPortalUrl,
		getTopupOptions,
		createTeamTopup,
		createTeam,
		getTeamTiers,
		getTeamStatus,
		inviteTeamMember,
		removeTeamMember,
		getModelBreakdown,
		getMyUsage,
		type BillingStatus,
		type TopupOption,
		type TeamTier,
		type TeamStatus,
		type ModelBreakdown,
		type MyUsage
	} from '$lib/apis/billing';
	import { PLAN_TIER, isCreditsUser as _isCreditsUser, isInternalUser } from '$lib/billing/planTiers';

	const i18n = getContext('i18n');

	let status: BillingStatus | null = null;
	let teamStatus: TeamStatus | null = null;
	let modelBreakdown: ModelBreakdown | null = null;
	let myUsage: MyUsage | null = null;
	let showCredits = false;
	let loading = true;

	let checkingOut = false;
	let openingPortal = false;

	// Create team modal
	let showCreateTeam = false;
	let teamName = '';
	let selectedSeatCount: number | null = null;
	let creatingTeam = false;
	let teamTiers: TeamTier[] = [];

	// Top-up modal
	let showTopupModal = false;
	let topupOptions: TopupOption[] = [];
	let toppingUp = false;

	// Invite modal
	let showInviteModal = false;
	let inviteEmail = '';
	let inviting = false;

	// Member actions
	let openMenuUserId: string | null = null;
	let pendingRemoveUserId: string | null = null;
	let pendingRemoveName: string | null = null;

	const currentMonth = new Date().toLocaleString('default', { month: 'long', year: 'numeric' });

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
			status = await getBillingStatus(localStorage.token);

			const extras: Promise<any>[] = [];

			if (status?.enabled) {
				extras.push(getModelBreakdown(localStorage.token).then((d) => (modelBreakdown = d)).catch(() => {}));
				extras.push(
					getMyUsage(localStorage.token)
						.then((d) => { myUsage = d; })
						.catch(() => {})
				);
			}

			if (status?.plan_tier === PLAN_TIER.TEAM) {
				extras.push(getTeamStatus(localStorage.token).then((d) => (teamStatus = d)).catch(() => {}));
				extras.push(getTopupOptions(localStorage.token).then((d) => (topupOptions = d)).catch(() => {}));
			}
			if (status?.plan_tier === PLAN_TIER.TRIAL || status?.plan_tier === PLAN_TIER.PRO || status?.plan_tier === PLAN_TIER.PREMIUM) {
				extras.push(getTeamTiers(localStorage.token).then((d) => (teamTiers = d)).catch(() => {}));
			}

			await Promise.all(extras);
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

	const handleInvite = async () => {
		if (!inviteEmail.trim()) return;
		inviting = true;
		try {
			await inviteTeamMember(localStorage.token, inviteEmail.trim());
			toast.success($i18n.t('Invite sent to {{email}}', { email: inviteEmail.trim() }));
			inviteEmail = '';
			showInviteModal = false;
			teamStatus = await getTeamStatus(localStorage.token);
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to send invite'));
		} finally {
			inviting = false;
		}
	};

	const handleRemoveMember = (userId: string, name: string) => {
		openMenuUserId = null;
		pendingRemoveUserId = userId;
		pendingRemoveName = name;
	};

	const confirmRemoveMember = async () => {
		if (!pendingRemoveUserId) return;
		const userId = pendingRemoveUserId;
		const name = pendingRemoveName ?? '';
		pendingRemoveUserId = null;
		pendingRemoveName = null;
		try {
			await removeTeamMember(localStorage.token, userId);
			toast.success($i18n.t('{{name}} removed from team', { name }));
			teamStatus = await getTeamStatus(localStorage.token);
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to remove member'));
		}
	};

	function initials(name: string) {
		return name
			.split(' ')
			.map((w) => w[0])
			.join('')
			.slice(0, 2)
			.toUpperCase();
	}

	const AVATAR_COLORS = [
		'bg-blue-500',
		'bg-purple-500',
		'bg-green-500',
		'bg-orange-500',
		'bg-pink-500',
		'bg-teal-500'
	];

	function avatarColor(str: string) {
		let h = 0;
		for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) & 0xffffffff;
		return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
	}

	$: isCreditsUser = _isCreditsUser(status?.plan_tier) && (myUsage?.credits_per_eur_cent ?? 0) > 0;
	$: showCredits = !!status && !isInternalUser(status.plan_tier) && status.plan_tier !== null;

	// What percentage of the credits balance has been consumed (0–100)
	$: creditsUsedPercentage =
		isCreditsUser && (myUsage?.credits_balance ?? 0) > 0
			? Math.min(100, ((myUsage?.credits_used ?? 0) / (myUsage?.credits_balance ?? 1)) * 100)
			: 0;

	$: trialPct = creditsUsedPercentage;
	$: trialLowBalance = trialPct >= 75;

	// Format a EUR cost as either "€0.0042" or "7 cr" depending on the active display tab
	$: formatCost = (eur: number) => {
		if (showCredits && myUsage && myUsage.credits_per_eur_cent > 0) {
			return `${Math.round(eur * 100 * myUsage.credits_per_eur_cent).toLocaleString()} cr`;
		}
		return `€${eur.toFixed(2)}`;
	};

	$: teamBudgetTotal =
		(status?.subscription_credits ?? 0) + (status?.topup_credits ?? 0);
	$: teamBudgetPct =
		teamBudgetTotal > 0
			? Math.min(100, (1 - (status?.credits_remaining ?? 0) / teamBudgetTotal) * 100)
			: 0;
	$: teamLowBalance = teamBudgetPct >= 75;

	$: activeMembers = teamStatus?.members?.length ?? 0;
	$: avgPerMember =
		activeMembers > 0 ? (teamStatus?.team_month_cost_eur ?? 0) / activeMembers : 0;
</script>

<div
	class="w-full flex-1 transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} overflow-y-auto h-screen"
>
<div
	class="w-full max-w-3xl mx-auto px-4 py-8 space-y-1"
	on:click={() => (openMenuUserId = null)}
	role="presentation"
>
	<!-- Page header -->
	<div class="mb-6">
		<h1 class="text-3xl font-bold tracking-tight">{$i18n.t('Billing & Usage')}</h1>
		<p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
			{$i18n.t('Manage your plan, credits and usage statistics.')}
		</p>
	</div>

	{#if loading}
		<div class="text-sm text-gray-400 animate-pulse">{$i18n.t('Loading...')}</div>

	{:else if !status?.enabled}
		<div class="rounded-xl border border-gray-200 dark:border-gray-700 p-6 text-sm text-gray-500 dark:text-gray-400">
			{$i18n.t('Billing is not enabled on this instance.')}
		</div>

	{:else}
		<div class="pb-2">
			<p class="text-xs text-gray-400 dark:text-gray-500 font-medium uppercase tracking-wide">
				{$i18n.t('Plan & subscription')}
			</p>
		</div>

		<!-- ===== INTERNAL ===== -->
		{#if status.plan_tier === PLAN_TIER.INTERNAL}
			<div class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6">
				<div class="flex items-start justify-between gap-4">
					<div>
						<h2 class="text-lg font-bold">{$i18n.t('Internal Plan')}</h2>
						<div class="text-2xl font-bold mt-2">€{status.current_month_cost_eur.toFixed(2)}</div>
						<div class="text-xs text-gray-400 dark:text-gray-500">{$i18n.t('Used this month')}</div>
					</div>
					<span class="mt-1 shrink-0 text-xs px-3 py-1 rounded-full border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 font-medium">
						{$i18n.t('Internal')}
					</span>
				</div>
				<p class="text-sm text-gray-500 dark:text-gray-400 mt-4">
					{$i18n.t('Your usage is tracked for internal purposes only. No billing applied to your account. To request a plan change or usage increase, contact your administrator.')}
				</p>
			</div>

		<!-- ===== FREE TRIAL ===== -->
		{:else if status.plan_tier === PLAN_TIER.TRIAL}
			<div class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6">
				<div class="flex items-start justify-between gap-4 flex-wrap">
					<div>
						<h2 class="text-lg font-bold">{$i18n.t('Free Trial')}</h2>
						<div class="flex items-center gap-2 mt-1">
							{#if showCredits && myUsage}
								<span class="text-sm {trialLowBalance ? 'text-red-500' : 'text-gray-500 dark:text-gray-400'}">
									{myUsage.credits_used.toLocaleString()} {$i18n.t('of')} {myUsage.credits_balance.toLocaleString()} {$i18n.t('credits used')}
								</span>
							{:else}
								<span class="text-sm {trialLowBalance ? 'text-red-500' : 'text-gray-500 dark:text-gray-400'}">
									€{status.credit_used_eur.toFixed(2)} {$i18n.t('of')} €{status.credit_limit_eur.toFixed(2)} {$i18n.t('used')}
								</span>
							{/if}
							{#if trialLowBalance}
								<span class="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400 font-medium">
									{$i18n.t('Low balance')}
								</span>
							{/if}
						</div>
					</div>
					<div class="flex items-center gap-2 shrink-0">
						<button
							on:click={handlePortal}
							disabled={openingPortal}
							class="px-4 py-2 rounded-full text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition font-medium disabled:opacity-50"
						>
							{openingPortal ? $i18n.t('Opening...') : $i18n.t('Manage Billing')}
						</button>
						{#if teamTiers.length > 0}
							<button
								on:click={() => (showCreateTeam = true)}
								class="px-4 py-2 rounded-full text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition font-medium"
							>
								{$i18n.t('View Plans')}
							</button>
						{/if}
						<button
							on:click={handleCheckout}
							disabled={checkingOut}
							class="px-4 py-2 rounded-full text-sm bg-blue-600 text-white hover:bg-blue-700 transition font-medium disabled:opacity-50"
						>
							{checkingOut ? $i18n.t('Redirecting...') : $i18n.t('Upgrade to Pro')}
						</button>
					</div>
				</div>

				<div class="mt-4 space-y-1.5">
					<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Credits used')}</div>
					<div class="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-2">
						<div
							class="h-2 rounded-full transition-all {trialLowBalance ? 'bg-red-500' : 'bg-green-500'}"
							style="width: {trialPct}%"
						></div>
					</div>
				</div>

				<p class="text-sm text-gray-500 dark:text-gray-400 mt-3">
					{#if isCreditsUser && myUsage && myUsage.credits_remaining <= 0}
						{$i18n.t('Your free exploratory credits are exhausted. Upgrade to continue using Hubgate.')}
					{:else if isCreditsUser && myUsage}
						{$i18n.t('You have')} <strong>{showCredits ? `${myUsage.credits_remaining.toLocaleString()} cr` : `€${status.credit_remaining_eur.toFixed(2)}`}</strong> {$i18n.t('remaining of your free exploratory credits. Upgrade to continue using Hubgate after your trial ends.')}
					{:else}
						{$i18n.t('You have')} <strong>€{status.credit_remaining_eur.toFixed(2)}</strong> {$i18n.t('remaining of your free exploratory credits. Upgrade to continue using Hubgate after your trial ends.')}
					{/if}
				</p>
			</div>

		<!-- ===== PAID ===== -->
		{:else if status.plan_tier === PLAN_TIER.PRO || status.plan_tier === PLAN_TIER.PREMIUM}
			<div class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6">
				<div class="flex items-start justify-between gap-4 flex-wrap">
					<div>
						<h2 class="text-lg font-bold">{$i18n.t('Pro Plan')}</h2>
						<div class="flex items-center gap-2 mt-1">
							{#if showCredits && myUsage}
								<span class="text-sm {creditsUsedPercentage >= 75 ? 'text-red-500' : 'text-gray-500 dark:text-gray-400'}">
									{myUsage.credits_used.toLocaleString()} {$i18n.t('of')} {myUsage.credits_balance.toLocaleString()} {$i18n.t('credits used')}
								</span>
								{#if myUsage.credits_remaining <= 0}
									<span class="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400 font-medium">
										{$i18n.t('Exhausted')}
									</span>
								{:else if creditsUsedPercentage >= 75}
									<span class="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400 font-medium">
										{$i18n.t('Low balance')}
									</span>
								{/if}
							{:else}
								<span class="text-sm text-gray-500 dark:text-gray-400">
									€{status.current_month_cost_eur.toFixed(2)} {$i18n.t('used this month')}
								</span>
							{/if}
						</div>
						{#if showCredits && myUsage}
							<div class="mt-3 space-y-1.5">
								<div class="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-2">
									<div
										class="h-2 rounded-full transition-all {creditsUsedPercentage >= 75 ? 'bg-red-500' : 'bg-green-500'}"
										style="width: {creditsUsedPercentage}%"
									></div>
								</div>
								<div class="text-xs text-gray-500 dark:text-gray-400">
									{myUsage.credits_remaining.toLocaleString()} {$i18n.t('credits remaining')}
								</div>
							</div>
						{/if}
					</div>
					<div class="flex items-center gap-2 shrink-0">
						<button
							on:click={handlePortal}
							disabled={openingPortal}
							class="px-4 py-2 rounded-full text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition font-medium disabled:opacity-50"
						>
							{openingPortal ? $i18n.t('Opening...') : $i18n.t('Manage Billing')}
						</button>
						{#if teamTiers.length > 0}
							<button
								on:click={() => (showCreateTeam = true)}
								class="px-4 py-2 rounded-full text-sm bg-blue-600 text-white hover:bg-blue-700 transition font-medium"
							>
								{$i18n.t('Start a Team')}
							</button>
						{/if}
					</div>
				</div>

				{#if status.subscription_status === 'past_due'}
					<div class="mt-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
						{$i18n.t('Your last payment failed. Please update your payment method to avoid service interruption.')}
					</div>
				{/if}
			</div>

		<!-- ===== TEAM OWNER ===== -->
		{:else if status.plan_tier === PLAN_TIER.TEAM}
			<div class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6">
				<div class="flex items-start justify-between gap-4 flex-wrap">
					<div>
						<h2 class="text-lg font-bold">{$i18n.t('Team Subscription')}</h2>
						<p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
							{$i18n.t("Your team is on a shared usage plan. Each member's activity is tracked individually.")}
						</p>
					</div>
					<div class="flex items-center gap-2 shrink-0">
						<button
							on:click={handleTeamPortal}
							disabled={openingPortal}
							class="px-4 py-2 rounded-full text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition font-medium disabled:opacity-50"
						>
							{openingPortal ? $i18n.t('Opening...') : $i18n.t('Manage Billing')}
						</button>
						{#if topupOptions.length > 0}
							<button
								on:click={() => (showTopupModal = true)}
								class="px-4 py-2 rounded-full text-sm bg-blue-600 text-white hover:bg-blue-700 transition font-medium"
							>
								{$i18n.t('Buy Credits')}
							</button>
						{/if}
					</div>
				</div>

				<!-- Usage bar -->
				<div class="mt-4 space-y-1.5">
					<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Credit used')}</div>
					<div class="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-2">
						{#if status.subscription_credits}
							<div
								class="h-2 rounded-full transition-all {teamLowBalance ? 'bg-red-500' : 'bg-green-500'}"
								style="width: {teamBudgetPct}%"
							></div>
						{:else}
							<div class="h-2 rounded-full bg-blue-500" style="width: 100%"></div>
						{/if}
					</div>
				</div>

				<!-- Stats row -->
				<div class="mt-4 grid grid-cols-3 gap-4">
					<div>
						<div class="text-xl font-bold">€{(status.team_month_cost_eur ?? 0).toFixed(2)}</div>
						<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Usage this month')}</div>
					</div>
					<div>
						<div class="text-xl font-bold">{activeMembers}</div>
						<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Active members')}</div>
					</div>
					<div>
						<div class="text-xl font-bold">€{avgPerMember.toFixed(2)}</div>
						<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Avg per member')}</div>
					</div>
				</div>

				{#if status.subscription_status === 'past_due'}
					<div class="mt-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
						{$i18n.t('Your last payment failed. Please update your payment method to avoid service interruption for your team.')}
					</div>
				{/if}
			</div>

		<!-- ===== TEAM MEMBER ===== -->
		{:else if status.plan_tier === PLAN_TIER.TEAM_MEMBER}
			<div class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6">
				<div class="flex items-start justify-between gap-4 flex-wrap">
					<div>
						<h2 class="text-lg font-bold">{status.team_name ?? $i18n.t('Team Plan')}</h2>
						{#if status.team_owner_name}
							<p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
								{$i18n.t('Managed by {{owner}}', { owner: status.team_owner_name })}
							</p>
						{/if}
					</div>
				</div>

			{#if status.subscription_credits}
				{@const total = (status.subscription_credits ?? 0) + (status.topup_credits ?? 0)}
				{@const pct = total > 0 ? Math.min(100, (1 - (status.credits_remaining ?? 0) / total) * 100) : 0}
					<div class="mt-4 space-y-1.5">
						<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Team budget used')}</div>
						<div class="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-2">
							<div
								class="h-2 rounded-full transition-all {pct >= 75 ? 'bg-red-500' : 'bg-green-500'}"
								style="width: {pct}%"
							></div>
						</div>
					</div>
				{/if}

				<div class="mt-4">
					<div class="text-xl font-bold">€{status.current_month_cost_eur.toFixed(2)}</div>
					<div class="text-xs text-gray-500 dark:text-gray-400">{$i18n.t('Your usage this month')}</div>
				</div>

				{#if status.subscription_status === 'past_due' || status.subscription_status === 'canceled'}
					<div class="mt-4 rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 px-4 py-3 text-sm text-yellow-700 dark:text-yellow-400">
						{$i18n.t('Your team subscription has an issue. Please contact your team owner.')}
					</div>
				{/if}
			</div>

		{:else}
			<div class="rounded-2xl border border-gray-200 dark:border-gray-700 p-6 text-sm text-gray-400">
				{$i18n.t('Your billing account is being set up. Please refresh in a moment.')}
			</div>
		{/if}

		<!-- ===== PER-MODEL BREAKDOWN ===== -->
		{#if modelBreakdown && modelBreakdown.models.length > 0}
			<div class="pt-6">
				<p class="text-xs text-gray-400 dark:text-gray-500 font-medium pb-3">
					{$i18n.t('Per-Model Cost Breakdown')} &bull; {modelBreakdown.month}
				</p>
				<div class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 overflow-hidden">
					{#each modelBreakdown.models as m}
						<div class="flex items-center justify-between px-5 py-3.5 border-b border-gray-100 dark:border-gray-800 last:border-b-0">
							<span class="text-sm font-medium">{m.model}</span>
							<div class="flex items-center gap-4">
								<span class="text-sm font-semibold">{formatCost(m.cost)}</span>
								<span class="text-xs text-gray-400 dark:text-gray-500 w-8 text-right">{m.pct}%</span>
							</div>
						</div>
					{/each}
					<div class="flex items-center justify-between px-5 py-3.5 bg-gray-50 dark:bg-gray-800/50">
						<span class="text-sm font-medium">{$i18n.t('Total spent')} – {modelBreakdown.month}</span>
						<span class="text-sm font-bold">{formatCost(modelBreakdown.total)}</span>
					</div>
				</div>
			</div>
		{/if}

		<!-- ===== TEAM LEADERBOARD (team owner only) ===== -->
		{#if status.plan_tier === PLAN_TIER.TEAM && teamStatus}
			<div class="pt-6">
				<div class="flex items-center justify-between pb-3">
					<p class="text-xs text-gray-400 dark:text-gray-500 font-medium">
						{$i18n.t('Team Usage Leaderboard')} &bull; {currentMonth}
					</p>
					{#if (teamStatus.seat_used < teamStatus.seat_limit)}
						<button
							on:click|stopPropagation={() => (showInviteModal = true)}
							class="flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400 hover:underline font-medium"
						>
							<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
							</svg>
							{$i18n.t('Invite Member')}
						</button>
					{/if}
				</div>

				<div class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 overflow-hidden">
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b border-gray-100 dark:border-gray-800">
								<th class="text-left px-5 py-3 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Member')}</th>
								<th class="text-left px-3 py-3 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Role')}</th>
								<th class="text-left px-3 py-3 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Status')}</th>
								<th class="text-left px-3 py-3 text-xs font-medium text-gray-500 dark:text-gray-400 hidden md:table-cell">{$i18n.t('Models Used')}</th>
								<th class="text-right px-5 py-3 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Total spend')}</th>
								<th class="px-3 py-3"></th>
							</tr>
						</thead>
						<tbody>
							{#each teamStatus.members as member}
								<tr class="border-b border-gray-100 dark:border-gray-800 last:border-b-0 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition">
									<td class="px-5 py-3">
										<div class="flex items-center gap-2.5">
											<div class="w-7 h-7 rounded-full {avatarColor(member.name)} flex items-center justify-center text-white text-xs font-semibold shrink-0">
												{initials(member.name)}
											</div>
											<div>
												<div class="font-medium text-sm leading-tight">{member.name}</div>
												<div class="text-xs text-gray-400 dark:text-gray-500">{member.email}</div>
											</div>
										</div>
									</td>
									<td class="px-3 py-3">
										{#if member.role === 'owner'}
											<span class="text-xs px-2 py-0.5 rounded-full bg-gray-900 text-white dark:bg-white dark:text-gray-900 font-medium">
												{$i18n.t('Admin')}
											</span>
										{:else}
											<span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300 font-medium">
												{$i18n.t('Member')}
											</span>
										{/if}
									</td>
									<td class="px-3 py-3">
										<span class="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 font-medium">
											{$i18n.t('Active')}
										</span>
									</td>
									<td class="px-3 py-3 hidden md:table-cell">
										<span class="text-xs text-gray-500 dark:text-gray-400">
											{member.models_used.length > 0 ? member.models_used.join(', ') : '–'}
										</span>
									</td>
									<td class="px-5 py-3 text-right font-semibold">
										€{member.current_month_cost_eur.toFixed(2)}
									</td>
									<td class="px-3 py-3 relative">
										{#if member.role !== 'owner'}
											<button
												on:click|stopPropagation={() => (openMenuUserId = openMenuUserId === member.user_id ? null : member.user_id)}
												aria-label={$i18n.t('Member actions')}
												class="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 transition text-gray-400"
											>
												<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
													<circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/>
												</svg>
											</button>
											{#if openMenuUserId === member.user_id}
												<div class="absolute right-8 top-2 z-10 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-lg py-1 min-w-[140px]">
													<button
														on:click|stopPropagation={() => handleRemoveMember(member.user_id, member.name)}
														class="w-full text-left px-4 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
													>
														{$i18n.t('Delete member')}
													</button>
												</div>
											{/if}
										{/if}
									</td>
								</tr>
							{/each}

							<!-- Pending invites -->
							{#each teamStatus.pending_invites as inv}
								<tr class="border-b border-gray-100 dark:border-gray-800 last:border-b-0 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition">
									<td class="px-5 py-3">
										<div class="flex items-center gap-2.5">
											<div class="w-7 h-7 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-gray-400 text-xs font-semibold shrink-0">
												?
											</div>
											<div>
												<div class="text-sm text-gray-500 dark:text-gray-400">{inv.invited_email}</div>
											</div>
										</div>
									</td>
									<td class="px-3 py-3">
										<span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300 font-medium">
											{$i18n.t('Member')}
										</span>
									</td>
									<td class="px-3 py-3">
										<span class="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400 font-medium">
											{$i18n.t('Pending')}
										</span>
									</td>
									<td class="px-3 py-3 hidden md:table-cell">
										<span class="text-xs text-gray-400">–</span>
									</td>
									<td class="px-5 py-3 text-right font-semibold text-gray-400">€0.00</td>
									<td class="px-3 py-3"></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		{/if}
	{/if}
</div>
</div>

<!-- ===== INVITE MODAL ===== -->
{#if showInviteModal}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
		on:click|self={() => (showInviteModal = false)}
		role="dialog"
		aria-modal="true"
	>
		<div class="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 space-y-4">
			<div class="flex items-center justify-between">
				<h2 class="font-semibold text-base">{$i18n.t('Invite to team')}</h2>
				<button
					on:click={() => (showInviteModal = false)}
					aria-label={$i18n.t('Close')}
					class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
				>
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			<div class="space-y-1.5">
				<label class="text-sm font-medium text-gray-700 dark:text-gray-300" for="invite-email">{$i18n.t('Email')}</label>
				<div class="relative">
					<svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
					</svg>
					<input
						id="invite-email"
						type="email"
						bind:value={inviteEmail}
						placeholder="jane@company.com"
						on:keydown={(e) => e.key === 'Enter' && handleInvite()}
						class="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
					/>
				</div>
			</div>

			<button
				on:click={handleInvite}
				disabled={inviting || !inviteEmail.trim()}
				class="w-full py-2.5 rounded-lg text-sm bg-blue-600 text-white hover:bg-blue-700 transition font-medium disabled:opacity-50"
			>
				{inviting ? $i18n.t('Sending...') : $i18n.t('Send Invite')}
			</button>
			<button
				on:click={() => (showInviteModal = false)}
				class="w-full text-center text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition"
			>
				{$i18n.t('Cancel')}
			</button>
		</div>
	</div>
{/if}

<!-- ===== CREATE TEAM MODAL ===== -->
{#if showCreateTeam}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
		on:click|self={() => (showCreateTeam = false)}
		role="dialog"
		aria-modal="true"
	>
		<div class="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 space-y-5">
			<div class="flex items-center justify-between">
				<h2 class="font-semibold text-base">{$i18n.t('Start a Team')}</h2>
				<button on:click={() => (showCreateTeam = false)} aria-label={$i18n.t('Close')} class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			<div class="space-y-1.5">
				<label class="text-sm font-medium" for="team-name">{$i18n.t('Team name')}</label>
				<input
					id="team-name"
					type="text"
					bind:value={teamName}
					placeholder={$i18n.t('e.g. Acme Corp')}
					class="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-transparent px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
				/>
			</div>

			<div class="space-y-1.5">
				<div class="text-sm font-medium">{$i18n.t('Seat plan')}</div>
				<div class="space-y-2">
					{#each [...teamTiers].sort((a, b) => a.seat_count - b.seat_count) as tier}
						<button
							on:click={() => (selectedSeatCount = tier.seat_count)}
							class="w-full flex items-center justify-between px-4 py-3 rounded-xl border-2 transition
								{selectedSeatCount === tier.seat_count
									? 'border-blue-600 bg-blue-50 dark:bg-blue-900/20'
									: 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800'}"
						>
							<div class="text-sm font-medium">{tier.seat_count} {$i18n.t('seats')}</div>
							<div class="text-sm font-semibold">€{tier.price_eur.toFixed(0)}/mo</div>
						</button>
					{/each}
				</div>
			</div>

			<button
				on:click={handleCreateTeam}
				disabled={creatingTeam || !teamName.trim() || !selectedSeatCount}
				class="w-full py-2.5 rounded-lg text-sm bg-blue-600 text-white hover:bg-blue-700 transition font-medium disabled:opacity-50"
			>
				{creatingTeam ? $i18n.t('Redirecting to checkout...') : $i18n.t('Continue to checkout')}
			</button>
		</div>
	</div>
{/if}

<!-- ===== TOP-UP MODAL ===== -->
{#if showTopupModal}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
		on:click|self={() => (showTopupModal = false)}
		role="dialog"
		aria-modal="true"
	>
		<div class="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 space-y-4">
			<div class="flex items-center justify-between">
				<h2 class="font-semibold text-base">{$i18n.t('Buy more usage')}</h2>
				<button on:click={() => (showTopupModal = false)} aria-label={$i18n.t('Close')} class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
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
						<span class="font-medium text-sm">€{opt.amount_eur.toFixed(0)} {$i18n.t('credits')}</span>
						<span class="text-sm text-gray-500 dark:text-gray-400">€{opt.amount_eur.toFixed(2)}</span>
					</button>
				{/each}
			</div>
			{#if toppingUp}
				<div class="text-center text-sm text-gray-400 animate-pulse">{$i18n.t('Redirecting to checkout...')}</div>
			{/if}
		</div>
	</div>
{/if}

<!-- ===== REMOVE MEMBER CONFIRM ===== -->
<svelte:window on:keydown={(e) => { if (e.key === 'Escape' && pendingRemoveUserId) { pendingRemoveUserId = null; pendingRemoveName = null; } }} />
{#if pendingRemoveUserId}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
		on:click|self={() => { pendingRemoveUserId = null; pendingRemoveName = null; }}
		role="dialog"
		aria-modal="true"
		tabindex="-1"
	>
		<div class="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 space-y-4">
			<h2 class="font-semibold text-base">{$i18n.t('Remove team member?')}</h2>
			<p class="text-sm text-gray-500 dark:text-gray-400">
				{$i18n.t('{{name}} will lose access to the team immediately.', { name: pendingRemoveName ?? '' })}
			</p>
			<div class="flex gap-2">
				<button
					on:click={confirmRemoveMember}
					class="flex-1 py-2.5 rounded-lg text-sm bg-red-600 text-white hover:bg-red-700 transition font-medium"
				>
					{$i18n.t('Remove')}
				</button>
				<button
					on:click={() => { pendingRemoveUserId = null; pendingRemoveName = null; }}
					class="flex-1 py-2.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 transition font-medium"
				>
					{$i18n.t('Cancel')}
				</button>
			</div>
		</div>
	</div>
{/if}
