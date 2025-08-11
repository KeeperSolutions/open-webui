<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	const i18n = getContext('i18n');

	export let isOpen = false;
	export let isUploading = false;

	const dispatch = createEventDispatcher<{
		close: void;
		upload: { file: File };
		cancel: void;
	}>();

	let selectedFile: File | null = null;

	function handleClose() {
		if (isUploading) {
			// If upload is in progress, dispatch cancel event
			dispatch('cancel');
		}
		selectedFile = null;
		dispatch('close');
	}

	function handleFileSelect(event: Event) {
		const input = event.target as HTMLInputElement;
		if (input.files?.length) {
			selectedFile = input.files[0];
		}
	}

	function handleUpload() {
		if (selectedFile) {
			dispatch('upload', { file: selectedFile });
		}
	}
</script>

{#if isOpen}
	<div class="fixed inset-0 bg-black/10 backdrop-blur-sm flex items-center justify-center z-50">
		<div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-full max-w-md">
			<h3 class="text-lg font-semibold mb-4 text-gray-900 dark:text-white">
				{$i18n.t('Upload File')}
			</h3>

			<div class="mb-4">
				<label for="file" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
					{$i18n.t('Select File')}
				</label>
				<input
					type="file"
					id="file"
					on:change={handleFileSelect}
					disabled={isUploading}
					class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md
                           focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700
                           dark:text-white"
				/>
			</div>

			<div class="flex justify-end gap-3">
				<button
					on:click={handleClose}
					class="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100
                           dark:hover:bg-gray-700 rounded transition"
				>
					{isUploading ? $i18n.t('Cancel Upload') : $i18n.t('Cancel')}
				</button>
				<button
					on:click={handleUpload}
					disabled={!selectedFile || isUploading}
					class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700
                           transition disabled:opacity-50 disabled:cursor-not-allowed
                           flex items-center gap-2"
				>
					{#if isUploading}
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
					{isUploading ? $i18n.t('Uploading...') : $i18n.t('Upload')}
				</button>
			</div>
		</div>
	</div>
{/if}
