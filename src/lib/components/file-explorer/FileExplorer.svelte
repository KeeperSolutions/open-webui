<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	const i18n = getContext('i18n');
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	let files: string[] = [];
	let currentPath = 'home/data';
	let isLoading = false;
	let error: string | null = null;

	// Helper function to determine if item is a file
	const isFile = (name: string) => name.includes('.');

	async function handleListFiles() {
		isLoading = true;
		error = null;

		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/fs/ls`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					path: currentPath
				})
			});

			if (!response.ok) {
				const errorData = await response.text();
				throw new Error(errorData);
			}

			const data = await response.json();
			files = data.files.filter((f: string) => f !== '.' && f !== '..');
			toast.success($i18n.t('Files retrieved successfully'));
		} catch (err) {
			console.error('Error fetching files:', err);
			error = err.message;
			toast.error($i18n.t('Failed to fetch files. Please try again.'));
		} finally {
			isLoading = false;
		}
	}

	onMount(() => {
		handleListFiles();
	});
</script>

<div class="container mx-auto p-4">
	<div class="bg-white dark:bg-gray-800 rounded-lg shadow">
		<div class="p-4 border-b border-gray-200 dark:border-gray-700">
			<h2 class="text-lg font-semibold text-gray-800 dark:text-white">
				{$i18n.t('File Explorer')}
			</h2>
			<p class="text-sm text-gray-600 dark:text-gray-400">{currentPath}</p>
		</div>

		<div class="p-4">
			<div class="flex items-center gap-4 mb-4">
				<button
					on:click={handleListFiles}
					class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition disabled:opacity-50"
					disabled={isLoading}
				>
					{#if isLoading}
						<div class="flex items-center gap-2">
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
							{$i18n.t('Loading...')}
						</div>
					{:else}
						{$i18n.t('Refresh')}
					{/if}
				</button>
			</div>

			{#if error}
				<div
					class="mb-4 p-3 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-md"
				>
					<p class="text-sm">{error}</p>
					<button
						class="text-sm underline mt-1 hover:text-red-700 dark:hover:text-red-300"
						on:click={handleListFiles}
					>
						{$i18n.t('Try again')}
					</button>
				</div>
			{/if}

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
						<div
							class="flex items-center p-2 rounded hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer"
							on:click={() => {
								if (!isFile(item)) {
									currentPath = `${currentPath}/${item}`;
									handleListFiles();
								}
							}}
						>
							<div class="mr-3">
								{#if isFile(item)}
									<!-- File Icon -->
									<svg
										xmlns="http://www.w3.org/2000/svg"
										class="h-5 w-5 text-gray-500"
										fill="none"
										viewBox="0 0 24 24"
										stroke="currentColor"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
										/>
									</svg>
								{:else}
									<!-- Folder Icon -->
									<svg
										xmlns="http://www.w3.org/2000/svg"
										class="h-5 w-5 text-yellow-500"
										fill="none"
										viewBox="0 0 24 24"
										stroke="currentColor"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"
										/>
									</svg>
								{/if}
							</div>
							<span class="text-gray-700 dark:text-gray-200">{item}</span>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>
</div>
