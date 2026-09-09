<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	import { connectConnector, getGoogleDriveStatus, disconnectGoogleDrive } from '$lib/apis/connectors';

	import Search from '../../icons/Search.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	const icons: Record<string, string> = {
		google_drive:
			'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 87.3 78" class="size-4 shrink-0"><path d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z" fill="#0066da"/><path d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z" fill="#00ac47"/><path d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z" fill="#ea4335"/><path d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z" fill="#00832d"/><path d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z" fill="#2684fc"/><path d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z" fill="#ffba00"/></svg>'
	};

	interface ConnectorItem {
		id: 'google_drive';
		name: string;
		description: string;
		connected: boolean;
		externalAccount: string | null;
	}

	let loading = true;
	let loadError = false;
	let search = '';

	let connectors: ConnectorItem[] = [
		{
			id: 'google_drive',
			name: 'Google Drive',
			description: 'Let the model search and read files from your Google Drive.',
			connected: false,
			externalAccount: null
		}
	];

	$: filteredConnectors = connectors
		.filter((c) => c.name.toLowerCase().includes(search.toLowerCase().trim()))
		.sort((a, b) => Number(b.connected) - Number(a.connected));

	const loadStatus = async () => {
		loading = true;
		loadError = false;
		const status = await getGoogleDriveStatus(localStorage.token).catch(() => null);
		if (status === null) {
			loadError = true;
		} else {
			connectors = connectors.map((c) =>
				c.id === 'google_drive'
					? { ...c, connected: status.connected ?? false, externalAccount: status.external_account ?? null }
					: c
			);
		}
		loading = false;
	};

	const connectHandler = async (id: ConnectorItem['id']) => {
		if (id === 'google_drive') {
			await connectConnector('/connectors/google-drive/connect');
			await loadStatus();
		}
	};

	const disconnectHandler = async (id: ConnectorItem['id']) => {
		if (id !== 'google_drive') return;

		const res = await disconnectGoogleDrive(localStorage.token).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (res) {
			connectors = connectors.map((c) =>
				c.id === 'google_drive' ? { ...c, connected: false, externalAccount: null } : c
			);

			if (res.revoked === false) {
				toast.warning(
					$i18n.t(
						"Disconnected, but we couldn't confirm Google revoked access. You can also remove it from your Google Account permissions."
					)
				);
			} else {
				toast.success($i18n.t('Google Drive disconnected'));
			}
		}
	};

	onMount(() => {
		loadStatus();
	});
</script>

<div id="tab-connectors" class="flex flex-col h-full justify-between text-sm">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$i18n.t('Connectors')}</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if loading}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{:else if loadError}
			<UserSettingSection title={$i18n.t('Manage Connectors')} first>
				<div class="flex items-center justify-between gap-2.5">
					<div class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t("Couldn't check connector status. Please try again.")}
					</div>
					<button
						class="shrink-0 px-3 py-1.5 text-xs font-medium bg-transparent hover:bg-gray-100 dark:hover:bg-gray-850 text-gray-700 dark:text-gray-300 transition rounded-full border border-gray-200 dark:border-gray-700"
						type="button"
						on:click={loadStatus}
					>
						{$i18n.t('Retry')}
					</button>
				</div>
			</UserSettingSection>
		{:else}
			<UserSettingSection title={$i18n.t('Manage Connectors')} first>
				<div
					class="flex items-center gap-1.5 h-7 px-2 mb-3 rounded-lg text-xs bg-gray-50/70 dark:bg-white/[0.03]"
				>
					<Search className="size-3.5" strokeWidth="1.5" />
					<label class="sr-only" for="search-input-connectors">{$i18n.t('Search')}</label>
					<input
						id="search-input-connectors"
						class="w-full text-xs bg-transparent py-1 outline-hidden dark:text-gray-300"
						bind:value={search}
						placeholder={$i18n.t('Search Connectors')}
					/>
				</div>

				<div class="flex flex-col gap-4 max-h-72 overflow-y-auto scrollbar-hover pr-1">
					{#each filteredConnectors as item (item.id)}
						<div class="flex items-center justify-between gap-2.5 py-1">
							<div class="flex items-center gap-2.5 min-w-0">
								{@html icons[item.id] ?? ''}
								<div class="min-w-0">
									<div class="text-sm text-gray-900 dark:text-white">{item.name}</div>
									<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">
										{#if item.connected}
											{$i18n.t('Connected as {{email}}', { email: item.externalAccount })}
										{:else}
											{item.description}
										{/if}
									</div>
								</div>
							</div>

							{#if item.connected}
								<button
									class="shrink-0 px-3 py-1.5 text-xs font-medium bg-transparent hover:bg-gray-100 dark:hover:bg-gray-850 text-gray-700 dark:text-gray-300 transition rounded-full border border-gray-200 dark:border-gray-700"
									type="button"
									on:click={() => disconnectHandler(item.id)}
								>
									{$i18n.t('Disconnect')}
								</button>
							{:else}
								<button
									class="shrink-0 px-3 py-1.5 text-xs font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
									type="button"
									on:click={() => connectHandler(item.id)}
								>
									{$i18n.t('Connect')}
								</button>
							{/if}
						</div>
					{:else}
						<div class="text-xs text-gray-500 dark:text-gray-400 py-2">
							{$i18n.t('No connectors found')}
						</div>
					{/each}
				</div>
			</UserSettingSection>
		{/if}
	</div>
</div>
