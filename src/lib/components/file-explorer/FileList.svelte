<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	interface FileItem {
		kind: 'file' | 'directory';
		path: string;
	}

	export let files: FileItem[] = [];
	export let selectedFile: FileItem | null = null;
	export let isLoading = false;
	export let error: string | null = null;

	// Emit events to parent
	import { createEventDispatcher } from 'svelte';
	const dispatch = createEventDispatcher<{
		select: { item: FileItem };
	}>();

	// Helper function to determine if item is a file
	const isFile = (item: FileItem) => item.kind === 'file';

	function handleItemClick(item: FileItem) {
		dispatch('select', { item });
	}
</script>

{#if isLoading}
	<div class="flex justify-center py-8">
		<div class="animate-pulse text-gray-400 dark:text-gray-600">
			{$i18n.t('Loading files...')}
		</div>
	</div>
{:else if files.length === 0 && !error}
	<div class="text-center py-8 text-gray-500 dark:text-gray-400">
		{$i18n.t('No files found')}
	</div>
{:else}
	<div class="grid gap-2">
		{#each files as item}
			{#if isFile(item)}
				<!-- File item -->
				<div
					class="flex items-center p-2 rounded transition-colors cursor-pointer
                        {selectedFile?.path === item.path
						? 'bg-blue-100 dark:bg-blue-900/30 border-2 border-blue-300 dark:border-blue-600'
						: 'hover:bg-gray-100 dark:hover:bg-gray-700 border-2 border-transparent'}"
					role="button"
					tabindex="0"
					aria-label="Select file: {item.path}"
					on:click={() => handleItemClick(item)}
					on:keydown={(event) => {
						if (event.key === 'Enter' || event.key === ' ') {
							event.preventDefault();
							handleItemClick(item);
						}
					}}
				>
					<div class="mr-3">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							class="h-5 w-5 {selectedFile?.path === item.path ? 'text-blue-600' : 'text-gray-500'}"
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
							aria-hidden="true"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
							/>
						</svg>
					</div>
					<div class="flex-1">
						<span
							class="text-gray-700 dark:text-gray-200 {selectedFile?.path === item.path
								? 'font-medium'
								: ''}">{item.path}</span
						>
						<div class="text-xs text-gray-500 dark:text-gray-400">
							File {selectedFile?.path === item.path ? '• Selected' : ''}
						</div>
					</div>
					{#if selectedFile?.path === item.path}
						<div class="ml-2">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								class="h-5 w-5 text-blue-600"
								fill="none"
								viewBox="0 0 24 24"
								stroke="currentColor"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M5 13l4 4L19 7"
								/>
							</svg>
						</div>
					{/if}
				</div>
			{:else}
				<!-- Folder item -->
				<div
					class="flex items-center p-2 rounded hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer border-2 border-transparent"
					role="button"
					tabindex="0"
					aria-label="Open folder: {item.path}"
					on:click={() => handleItemClick(item)}
					on:keydown={(event) => {
						if (event.key === 'Enter' || event.key === ' ') {
							event.preventDefault();
							handleItemClick(item);
						}
					}}
				>
					<div class="mr-3">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							class="h-5 w-5 text-yellow-500"
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
							aria-hidden="true"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"
							/>
						</svg>
					</div>
					<div class="flex-1">
						<span class="text-gray-700 dark:text-gray-200">{item.path}</span>
						<div class="text-xs text-gray-500 dark:text-gray-400">Directory</div>
					</div>
					<div class="ml-2">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							class="h-4 w-4 text-gray-400"
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M9 5l7 7-7 7"
							/>
						</svg>
					</div>
				</div>
			{/if}
		{/each}
	</div>
{/if}
