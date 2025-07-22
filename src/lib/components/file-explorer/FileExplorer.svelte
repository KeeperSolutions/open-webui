<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	const i18n = getContext('i18n');
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	interface FileItem {
		kind: 'file' | 'directory';
		path: string;
	}

	let files: FileItem[] = [];
	let currentPath = 'home/data';
	let isLoading = false;
	let error: string | null = null;
	let selectedFile: FileItem | null = null;
	let isDownloading = false;

	// Add new state variables
	let isCreateFolderModalOpen = false;
	let newFolderName = '';
	let isCreatingFolder = false;

	// Helper function to determine if item is a file
	const isFile = (item: FileItem) => item.kind === 'file';

	async function handleListFiles() {
		isLoading = true;
		error = null;
		selectedFile = null; // Clear selection when navigating

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
			// Filter out current and parent directory entries
			files = data.files.filter((f: FileItem) => f.path !== '.' && f.path !== '..');
			toast.success($i18n.t('Files retrieved successfully'));
		} catch (err) {
			console.error('Error fetching files:', err);
			error = err.message;
			toast.error($i18n.t('Failed to fetch files. Please try again.'));
		} finally {
			isLoading = false;
		}
	}

	function handleItemClick(item: FileItem) {
		if (isFile(item)) {
			// Select file
			selectedFile = selectedFile?.path === item.path ? null : item; // Toggle selection
		} else {
			// Navigate to folder
			selectedFile = null; // Clear selection
			currentPath = `${currentPath}/${item.path}`;
			handleListFiles();
		}
	}

	// Add this helper function at the top of your script section
	function getMimeType(filename: string): string {
		const extension = filename.split('.').pop()?.toLowerCase() || '';
		const mimeTypes: Record<string, string> = {
			// Common document formats
			pdf: 'application/pdf',
			txt: 'text/plain',
			md: 'text/markdown',
			doc: 'application/msword',
			docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
			xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
			xls: 'application/vnd.ms-excel',

			// Image formats
			png: 'image/png',
			jpg: 'image/jpeg',
			jpeg: 'image/jpeg',
			gif: 'image/gif',
			webp: 'image/webp',

			// Data formats
			json: 'application/json',
			xml: 'application/xml',
			csv: 'text/csv',

			// Archives
			zip: 'application/zip',
			tar: 'application/x-tar',
			gz: 'application/gzip',

			// Code files
			js: 'text/javascript',
			ts: 'text/typescript',
			py: 'text/x-python',
			html: 'text/html',
			css: 'text/css'
		};

		return mimeTypes[extension] || 'application/octet-stream';
	}

	async function handleDownload() {
		if (!selectedFile) return;

		const fullPath = `${currentPath}/${selectedFile.path}`;
		isDownloading = true;

		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/fs/cat`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					path: fullPath
				})
			});

			if (!response.ok) {
				throw new Error('Download failed');
			}

			const data = await response.json();
			const base64Content = data.content.data;

			// Convert base64 to binary
			const binaryString = atob(base64Content);
			const bytes = new Uint8Array(binaryString.length);
			for (let i = 0; i < binaryString.length; i++) {
				bytes[i] = binaryString.charCodeAt(i);
			}

			// Get the appropriate MIME type for the file
			const mimeType = getMimeType(selectedFile.path);

			// Create blob with correct MIME type
			const blob = new Blob([bytes], { type: mimeType });
			const url = window.URL.createObjectURL(blob);

			const a = document.createElement('a');
			a.href = url;
			a.download = selectedFile.path;
			document.body.appendChild(a);
			a.click();
			window.URL.revokeObjectURL(url);
			document.body.removeChild(a);

			toast.success(`Downloaded: ${selectedFile.path}`);
		} catch (err) {
			console.error('Download error:', err);
			toast.error(`Download failed: ${err.message}`);
		} finally {
			isDownloading = false;
		}
	}

	function handleBackNavigation() {
		const pathParts = currentPath.split('/');
		pathParts.pop();
		currentPath = pathParts.join('/') || 'home/data';
		handleListFiles();
	}

	function openCreateFolderModal() {
		newFolderName = '';
		isCreateFolderModalOpen = true;
	}

	function closeCreateFolderModal() {
		isCreateFolderModalOpen = false;
		newFolderName = '';
	}

	async function handleCreateFolder() {
		if (!newFolderName.trim()) return;

		isCreatingFolder = true;
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/fs/mkdir`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					path: `${currentPath}/${newFolderName}`
				})
			});

			if (!response.ok) {
				const errorData = await response.json();
				// Extract the specific error message from the API response
				const errorMessage = errorData.detail?.[0]?.msg || 'Failed to create folder';
				throw new Error(errorMessage);
			}

			await response.json();
			toast.success($i18n.t('Folder created successfully'));
			closeCreateFolderModal();
			handleListFiles(); // Refresh the file list
		} catch (err) {
			console.error('Error creating folder:', err);
			toast.error(err.message); // Display the specific error message
		} finally {
			isCreatingFolder = false;
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
			<div class="mr-3 flex items-center gap-2">
				<!-- Folder Icon -->
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
				<p class="text-sm text-gray-600 dark:text-gray-400">{currentPath}</p>
			</div>
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

				<!-- Back button for navigation -->
				{#if currentPath !== 'home/data'}
					<button
						on:click={handleBackNavigation}
						class="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 transition"
					>
						← {$i18n.t('Back')}
					</button>
				{/if}

				<!-- Download button - only show when file is selected -->
				{#if selectedFile}
					<button
						on:click={handleDownload}
						class="px-4 py-2 rounded transition flex items-center gap-2 min-w-[200px] h-[40px] justify-center {isDownloading
							? 'bg-gray-300 text-gray-700 hover:bg-gray-400'
							: 'bg-green-600 text-white hover:bg-green-700'}"
						aria-label="Download {selectedFile.path}"
						disabled={isDownloading}
					>
						<span class="w-4 h-4 flex-shrink-0">
							{#if isDownloading}
								<svg
									class="animate-spin w-full h-full"
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
							{:else}
								<svg
									xmlns="http://www.w3.org/2000/svg"
									class="w-full h-full"
									fill="none"
									viewBox="0 0 24 24"
									stroke="currentColor"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
									/>
								</svg>
							{/if}
						</span>
						<span class="flex-shrink-0">
							{isDownloading ? 'Downloading...' : `Download "${selectedFile.path}"`}
						</span>
					</button>
				{/if}

				<!-- New Folder button -->
				<button
					on:click={openCreateFolderModal}
					class="px-4 py-2 bg-orange-600 text-white rounded hover:bg-orange-700 transition flex items-center gap-2"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="h-5 w-5"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M12 6v6m0 0v6m0-6h6m-6 0H6"
						/>
					</svg>
					{$i18n.t('New Folder')}
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

			{#if selectedFile}
				<div
					class="mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-md"
				>
					<p class="text-sm">
						📄 {$i18n.t('Selected')}: <span class="font-medium">{selectedFile.path}</span>
					</p>
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
						{#if isFile(item)}
							<!-- Interactive file item with selection -->
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
									<!-- File Icon -->
									<svg
										xmlns="http://www.w3.org/2000/svg"
										class="h-5 w-5 {selectedFile?.path === item.path
											? 'text-blue-600'
											: 'text-gray-500'}"
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
										<!-- Selection indicator -->
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
							<!-- Interactive folder item -->
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
									<!-- Folder Icon -->
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
									<!-- Arrow indicator -->
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
		</div>
	</div>
</div>

<!-- Add the modal -->
{#if isCreateFolderModalOpen}
	<div class="fixed inset-0 bg-black/10 backdrop-blur-sm flex items-center justify-center z-50">
		<div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-full max-w-md">
			<h3 class="text-lg font-semibold mb-4 text-gray-900 dark:text-white">
				{$i18n.t('Create New Folder')}
			</h3>

			<div class="mb-4">
				<label
					for="folderName"
					class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2"
				>
					{$i18n.t('Folder Name')}
				</label>
				<input
					type="text"
					id="folderName"
					bind:value={newFolderName}
					maxlength="24"
					class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md
						   focus:outline-none focus:ring-2 focus:ring-orange-500 dark:bg-gray-700
						   dark:text-white"
					placeholder={$i18n.t('Enter folder name')}
					disabled={isCreatingFolder}
				/>
				<p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
					{newFolderName.length}/24 {$i18n.t('characters')}
				</p>
			</div>

			<div class="flex justify-end gap-3">
				<button
					on:click={closeCreateFolderModal}
					class="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100
						   dark:hover:bg-gray-700 rounded transition"
					disabled={isCreatingFolder}
				>
					{$i18n.t('Cancel')}
				</button>
				<button
					on:click={handleCreateFolder}
					disabled={!newFolderName.trim() || isCreatingFolder}
					class="px-4 py-2 bg-orange-600 text-white rounded hover:bg-orange-700
						   transition disabled:opacity-50 disabled:cursor-not-allowed
						   flex items-center gap-2"
				>
					{#if isCreatingFolder}
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
					{isCreatingFolder ? $i18n.t('Creating...') : $i18n.t('Create')}
				</button>
			</div>
		</div>
	</div>
{/if}
