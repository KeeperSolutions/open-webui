<script lang="ts">
	import { getContext } from 'svelte';
	import { connectConnector } from '$lib/apis/connectors';

	const i18n = getContext('i18n');

	export let connectorSuggestions = [];

	const icons: Record<string, string> = {
		google_drive:
			'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 87.3 78" class="size-4 shrink-0"><path d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z" fill="#0066da"/><path d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z" fill="#00ac47"/><path d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z" fill="#ea4335"/><path d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z" fill="#00832d"/><path d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z" fill="#2684fc"/><path d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z" fill="#ffba00"/></svg>'
	};
</script>

{#if connectorSuggestions.length > 0}
	<div class="mt-1 mb-2 w-full flex flex-col gap-1.5">
		{#each connectorSuggestions as suggestion (suggestion.connector)}
			<div
				class="flex items-center justify-between gap-2.5 px-3 py-2 rounded-xl border border-gray-100 dark:border-gray-800"
			>
				<div class="flex items-center gap-2.5 min-w-0">
					{@html icons[suggestion.icon] ?? ''}
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
