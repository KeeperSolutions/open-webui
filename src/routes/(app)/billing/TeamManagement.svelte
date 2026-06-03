<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getTeamStatus,
		inviteTeamMember,
		removeTeamMember,
		type TeamMember,
		type TeamInvite,
		type TeamStatus
	} from '$lib/apis/billing';

	const i18n = getContext('i18n');

	export let onClose: () => void = () => {};

	let teamStatus: TeamStatus | null = null;
	let loading = true;
	let inviteEmail = '';
	let inviting = false;
	let copiedToken: string | null = null;
	let emailError = '';

	const isValidEmail = (email: string) =>
		/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());

	const load = async () => {
		loading = true;
		try {
			teamStatus = await getTeamStatus(localStorage.token);
		} catch (e: any) {
			toast.error(e?.message ?? 'Failed to load team info');
		} finally {
			loading = false;
		}
	};

	onMount(() => { load(); });

	const handleInvite = async () => {
		if (!inviteEmail.trim()) return;
		if (!isValidEmail(inviteEmail)) {
			emailError = $i18n.t('Please enter a valid email address.');
			return;
		}
		emailError = '';
		inviting = true;
		try {
			const res = await inviteTeamMember(localStorage.token, inviteEmail.trim());
			toast.success($i18n.t('Invite sent to {{email}}', { email: res.invited_email }));
			inviteEmail = '';
			await load();
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to send invite'));
		} finally {
			inviting = false;
		}
	};

	const handleRemove = async (member: TeamMember) => {
		if (!confirm($i18n.t('Remove {{name}} from the team? They will revert to a trial plan.', { name: member.name }))) return;
		try {
			await removeTeamMember(localStorage.token, member.user_id);
			toast.success($i18n.t('{{name}} removed from team', { name: member.name }));
			await load();
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to remove member'));
		}
	};

	const copyInviteLink = (inv: TeamInvite) => {
		const url = `${window.location.origin}/invite/${inv.token}`;
		navigator.clipboard.writeText(url).then(() => {
			copiedToken = inv.token;
			setTimeout(() => (copiedToken = null), 2000);
		}).catch(() => {
			toast.error($i18n.t('Failed to copy link'));
		});
	};

	const formatDate = (ts: number) =>
		new Date(ts * 1000).toLocaleDateString('default', {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		});
</script>

<!-- Modal overlay -->
<div
	class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
	on:click|self={onClose}
	role="dialog"
	aria-modal="true"
>
	<div class="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-2xl mx-4 max-h-[90vh] flex flex-col">
		<!-- Header -->
		<div class="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
			<h2 class="font-semibold text-lg">{$i18n.t('Manage Team')}</h2>
			<button
				on:click={onClose}
				class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
				aria-label="Close"
			>
				<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		<div class="overflow-y-auto flex-1 px-6 py-5 space-y-6">
			{#if loading}
				<div class="text-sm text-gray-500 dark:text-gray-400 animate-pulse">{$i18n.t('Loading...')}</div>
			{:else if teamStatus}
				<!-- Seat usage -->
				<div class="flex items-center gap-3">
					<div class="text-sm text-gray-500 dark:text-gray-400">{$i18n.t('Seats used')}</div>
					<div class="text-sm font-medium">{teamStatus.seat_used} / {teamStatus.seat_limit}</div>
					<div class="flex-1 bg-gray-200 dark:bg-gray-700 rounded-full h-1.5">
						<div
							class="h-1.5 rounded-full bg-black dark:bg-white transition-all"
							style="width: {Math.min(100, (teamStatus.seat_used / teamStatus.seat_limit) * 100)}%"
						></div>
					</div>
				</div>

				<!-- Team aggregate usage -->
				<div class="text-sm text-gray-500 dark:text-gray-400">
					{$i18n.t('Team usage this month')}:
					<span class="font-semibold text-gray-900 dark:text-white ml-1">
						€{teamStatus.team_month_cost_eur.toFixed(4)}
					</span>
				</div>

				<!-- Members list -->
				<div class="space-y-2">
					<div class="text-sm font-medium">{$i18n.t('Members')}</div>
					<div class="rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
						<table class="w-full text-sm">
							<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
								<tr>
									<th class="text-left px-4 py-2 font-medium">{$i18n.t('Name')}</th>
									<th class="text-left px-4 py-2 font-medium">{$i18n.t('Email')}</th>
									<th class="text-right px-4 py-2 font-medium">{$i18n.t('This month')}</th>
									<th class="px-4 py-2"></th>
								</tr>
							</thead>
							<tbody class="divide-y divide-gray-100 dark:divide-gray-800">
								{#each teamStatus.members as member}
									<tr class="hover:bg-gray-50 dark:hover:bg-gray-850 transition">
										<td class="px-4 py-2">
											<div class="flex items-center gap-2">
												{member.name}
												{#if member.role === 'owner'}
													<span class="text-xs px-1.5 py-0.5 rounded-full bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">
														{$i18n.t('Owner')}
													</span>
												{/if}
											</div>
										</td>
										<td class="px-4 py-2 text-gray-500 dark:text-gray-400">{member.email}</td>
										<td class="px-4 py-2 text-right">€{member.current_month_cost_eur.toFixed(4)}</td>
										<td class="px-4 py-2 text-right">
											{#if member.role !== 'owner'}
												<button
													on:click={() => handleRemove(member)}
													class="text-xs text-red-500 hover:text-red-700 dark:hover:text-red-400 transition"
												>
													{$i18n.t('Remove')}
												</button>
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</div>

				<!-- Invite form -->
				{#if teamStatus.seat_used < teamStatus.seat_limit}
					<div class="space-y-2">
						<div class="text-sm font-medium">{$i18n.t('Invite a member')}</div>
						<div class="flex gap-2">
							<input
								type="email"
								bind:value={inviteEmail}
								on:input={() => (emailError = '')}
								placeholder={$i18n.t('Email address')}
								class="flex-1 text-sm rounded-lg border {emailError ? 'border-red-400 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'} bg-transparent px-3 py-2 focus:outline-none focus:ring-2 focus:ring-black dark:focus:ring-white"
								on:keydown={(e) => e.key === 'Enter' && handleInvite()}
							/>
							<button
								on:click={handleInvite}
								disabled={inviting || !inviteEmail.trim()}
								class="px-4 py-2 rounded-lg text-sm bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition disabled:opacity-50"
							>
								{inviting ? $i18n.t('Sending...') : $i18n.t('Send invite')}
							</button>
						</div>
						{#if emailError}
							<div class="text-xs text-red-500 dark:text-red-400">{emailError}</div>
						{/if}
					</div>
				{:else}
					<div class="text-sm text-gray-500 dark:text-gray-400">
						{$i18n.t('All seats are occupied. Remove a member to invite someone new.')}
					</div>
				{/if}

				<!-- Pending invites -->
				{#if teamStatus.pending_invites.length > 0}
					<div class="space-y-2">
						<div class="text-sm font-medium">{$i18n.t('Pending invites')}</div>
						<div class="space-y-1.5">
							{#each teamStatus.pending_invites as inv}
								<div class="flex items-center justify-between rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2.5 text-sm">
									<div>
										<div class="font-medium">{inv.invited_email}</div>
										<div class="text-xs text-gray-400 dark:text-gray-500">
											{$i18n.t('Expires')} {formatDate(inv.expires_at)}
										</div>
									</div>
									<button
										on:click={() => copyInviteLink(inv)}
										class="text-xs text-blue-600 dark:text-blue-400 hover:underline ml-4 shrink-0"
									>
										{copiedToken === inv.token ? $i18n.t('Copied!') : $i18n.t('Copy link')}
									</button>
								</div>
							{/each}
						</div>
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>
