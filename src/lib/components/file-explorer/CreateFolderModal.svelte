<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	export let isOpen = false;
	export let isCreating = false;
	export let folderName = '';

	import { createEventDispatcher } from 'svelte';
	const dispatch = createEventDispatcher<{
		close: void;
		create: { name: string };
	}>();

	function handleClose() {
		dispatch('close');
	}

	function handleCreate() {
		if (folderName.trim()) {
			dispatch('create', { name: folderName });
		}
	}
</script>

{#if isOpen}
	<div class="fixed inset-0 bg-black/10 backdrop-blur-sm flex items-center justify-center z-50">
		<div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-full max-w-md">
			<h3 class="text-lg font-semibold mb-4 text-gray-900 dark:text-white">
				{$i18n.t('Create New Secure Folder')}
			</h3>

			<div class="mb-4">
				<label
					for="folderName"
					class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2"
				>
					{$i18n.t('Secure Folder Name')}
				</label>
				<input
					type="text"
					id="folderName"
					bind:value={folderName}
					maxlength="24"
					class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md
                           focus:outline-none focus:ring-2 focus:ring-orange-500 dark:bg-gray-700
                           dark:text-white"
					placeholder={$i18n.t('Enter folder name')}
					disabled={isCreating}
				/>
				<p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
					{folderName.length}/24 {$i18n.t('characters')}
				</p>
			</div>

			<div class="flex justify-end gap-3">
				<button
					on:click={handleClose}
					class="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100
                           dark:hover:bg-gray-700 rounded transition"
					disabled={isCreating}
				>
					{$i18n.t('Cancel')}
				</button>
				<button
					on:click={handleCreate}
					disabled={!folderName.trim() || isCreating}
					class="px-4 py-2 bg-orange-600 text-white rounded hover:bg-orange-700
                           transition disabled:opacity-50 disabled:cursor-not-allowed
                           flex items-center gap-2"
				>
					{#if isCreating}
						<svg
							class="animate-spin h-4 w-4"
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
						>
							<circle
								class="opacity-25"
								cx="12"
								cy="12"
								r="10"
								stroke="currentColor"
								stroke-width="4"
							></circle>
							<path
								class="opacity-75"
								fill="currentColor"
								d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
							></path>
						</svg>
					{/if}
					{isCreating ? $i18n.t('Creating...') : $i18n.t('Create')}
				</button>
			</div>
		</div>
	</div>
{/if}
