<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	const i18n = getContext('i18n');

	import { WEBUI_API_BASE_URL } from '$lib/constants';

	async function handleListFiles() {
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/fs/ls`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					path: 'home/data'
				})
			});

			if (!response.ok) {
				const error = await response.text();
				throw new Error(error);
			}

			const data = await response.json();
			console.log('Files list:', data.files);

			toast.success($i18n.t('Files retrieved successfully'));
			return data.files;
		} catch (error) {
			console.error('Error fetching files:', error);
			toast.error($i18n.t('Failed to fetch files. Please try again.'));
		}
	}
</script>

<div class="container mx-auto p-4">
	<h1 class="text-2xl font-bold mb-4 text-gray-800 dark:text-gray-200">
		{$i18n.t('Hello World')}
	</h1>
	<p class="text-gray-600 dark:text-gray-400">
		{$i18n.t('Welcome to the Confidios page')}
	</p>
	<button
		on:click={handleListFiles}
		class="mt-4 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition"
	>
		{$i18n.t('List Files')}
	</button>
</div>
