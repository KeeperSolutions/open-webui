<script lang="ts">
	import { getContext } from 'svelte';
	import { connectConnector } from '$lib/apis/connectors';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';

	const i18n = getContext('i18n');

	export let connectorSuggestions = [];

	const icons: Record<string, any> = {
		google_drive: GoogleDrive
	};
</script>

{#if connectorSuggestions.length > 0}
	<div class="mt-1 mb-2 w-full flex flex-col gap-1.5">
		{#each connectorSuggestions as suggestion (suggestion.connector)}
			<div
				class="flex items-center justify-between gap-2.5 px-3 py-2 rounded-xl border border-gray-100 dark:border-gray-800"
			>
				<div class="flex items-center gap-2.5 min-w-0">
					<svelte:component this={icons[suggestion.icon]} />
					<div class="min-w-0">
						<div class="text-sm text-gray-900 dark:text-white">{suggestion.name}</div>
						<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">
							{suggestion.description}
						</div>
					</div>
				</div>

				<button
					class="shrink-0 px-3 py-1.5 text-xs font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
					type="button"
					on:click={() => connectConnector(suggestion.connect_url)}
				>
					{$i18n.t('Connect')}
				</button>
			</div>
		{/each}
	</div>
{/if}
