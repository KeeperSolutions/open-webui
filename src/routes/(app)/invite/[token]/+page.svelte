<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { getInviteInfo, acceptInvite, declineInvite, type InviteInfo } from '$lib/apis/billing';

	const i18n = getContext('i18n');

	let info: InviteInfo | null = null;
	let loading = true;
	let error: string | null = null;
	let accepting = false;
	let declining = false;

	let token: string;
	$: token = $page.params.token;

	onMount(async () => {
		try {
			info = await getInviteInfo(localStorage.token, token);
		} catch (e: any) {
			error = e?.message ?? 'Failed to load invite';
		} finally {
			loading = false;
		}
	});

	const handleAccept = async () => {
		accepting = true;
		try {
			await acceptInvite(localStorage.token, token);
			toast.success($i18n.t('You have joined {{team}}!', { team: info?.team_name ?? 'the team' }));
			goto('/billing');
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to accept invite'));
			accepting = false;
		}
	};

	const handleDecline = async () => {
		declining = true;
		try {
			await declineInvite(localStorage.token, token);
			toast.info($i18n.t('Invite declined.'));
			goto('/');
		} catch (e: any) {
			toast.error(e?.message ?? $i18n.t('Failed to decline invite'));
			declining = false;
		}
	};
</script>

<div class="w-full max-w-md mx-auto px-4 py-16 flex flex-col items-center text-center space-y-6">
	{#if loading}
		<div class="text-sm text-gray-500 dark:text-gray-400 animate-pulse">{$i18n.t('Loading...')}</div>

	{:else if error}
		<div class="rounded-xl border border-red-200 dark:border-red-800 p-6 space-y-2">
			<div class="font-semibold text-red-600 dark:text-red-400">{$i18n.t('Invite unavailable')}</div>
			<div class="text-sm text-gray-500 dark:text-gray-400">{error}</div>
		</div>
		<a href="/" class="text-sm text-blue-600 dark:text-blue-400 hover:underline">{$i18n.t('Go home')}</a>

	{:else if info}
		<div class="rounded-2xl border border-gray-200 dark:border-gray-700 p-8 space-y-5 w-full">
			<div class="space-y-1">
				<div class="text-2xl font-semibold">{info.team_name}</div>
				{#if info.owner_name}
					<div class="text-sm text-gray-500 dark:text-gray-400">
						{$i18n.t('Invited by {{owner}}', { owner: info.owner_name })}
					</div>
				{/if}
			</div>

			<p class="text-sm text-gray-600 dark:text-gray-400">
				{$i18n.t('You have been invited to join this team. Accept to get access under the team plan.')}
			</p>

			<div class="flex gap-3 justify-center">
				<button
					on:click={handleAccept}
					disabled={accepting || declining}
					class="px-6 py-2.5 rounded-lg text-sm bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition disabled:opacity-50"
				>
					{accepting ? $i18n.t('Joining...') : $i18n.t('Accept invite')}
				</button>
				<button
					on:click={handleDecline}
					disabled={accepting || declining}
					class="px-6 py-2.5 rounded-lg text-sm border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition disabled:opacity-50"
				>
					{declining ? $i18n.t('Declining...') : $i18n.t('Decline')}
				</button>
			</div>
		</div>
	{/if}
</div>
