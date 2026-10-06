<script lang="ts">
	import { getContext } from 'svelte';
	import { showControls, showDocumentList, type ChatDocument } from '$lib/stores';

	import DocumentCard from './Messages/DocumentCard.svelte';
	import { panelIconButtonClass } from './DocumentPreview.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import XMark from '../icons/XMark.svelte';

	const i18n = getContext('i18n');

	export let documents: ChatDocument[] = [];

	const close = () => {
		showDocumentList.set(false);
		showControls.set(false);
	};
</script>

<div class="w-full h-full flex flex-col min-h-0">
	<div class="flex items-center justify-between gap-2 p-2.5 text-gray-900 dark:text-white shrink-0">
		<div class="text-sm pl-1">
			{$i18n.t('Files')}<span class="ml-1 text-gray-500 dark:text-gray-400"
				>· {documents.length}</span
			>
		</div>

		<Tooltip content={$i18n.t('Close')}>
			<button
				class={panelIconButtonClass}
				type="button"
				aria-label={$i18n.t('Close')}
				on:click={close}
			>
				<XMark className="size-4" />
			</button>
		</Tooltip>
	</div>

	<div class="flex-1 min-h-0 overflow-y-auto px-2.5 pb-2.5">
		{#if documents.length > 0}
			<DocumentCard {documents} />
		{:else}
			<div class="h-full flex items-center justify-center text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('No files in this chat yet.')}
			</div>
		{/if}
	</div>
</div>
