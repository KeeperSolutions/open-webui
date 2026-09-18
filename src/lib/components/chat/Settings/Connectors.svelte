<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	import { connectConnector, getGoogleDriveStatus, disconnectGoogleDrive } from '$lib/apis/connectors';

	import Search from '../../icons/Search.svelte';
	import GoogleDrive from '../../icons/GoogleDrive.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	const icons: Record<string, any> = {
		google_drive: GoogleDrive
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
								<svelte:component this={icons[item.id]} />
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
