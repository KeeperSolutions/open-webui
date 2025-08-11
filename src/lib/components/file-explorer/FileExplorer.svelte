<script lang="ts">
	import { getContext, onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	const i18n = getContext('i18n');
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import 'tippy.js/dist/tippy.css';
	import 'tippy.js/themes/light.css';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import FileList from './FileList.svelte';
	import CreateFolderModal from './CreateFolderModal.svelte';
	import { confidiosStore } from '$lib/stores/integrations';
	import { handleConfidiosLogin, handleConfidiosLogout } from '$lib/utils/integrations/confidios';
	import { debounce } from 'lodash';

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
	let isConfidiosLoading = false;

	// Add new state variables
	let isCreateFolderModalOpen = false;
	let newFolderName = '';
	let isCreatingFolder = false;

	// Add root path state to track which view we're in
	let isGroupView = false;

	// Request tracking for race condition prevention
	let currentListRequest: AbortController | null = null;
	let currentDownloadRequest: AbortController | null = null;
	let requestCounter = 0; // Track request order
	let isMounted = true; // Track component mount state

	// Helper function to determine if item is a file
	const isFile = (item: FileItem) => item.kind === 'file';

	// Handle login action
	async function handleLogin() {
		if (!isMounted) return;

		isConfidiosLoading = true;
		try {
			const success = await handleConfidiosLogin();
			if (success && isMounted) {
				// User is logged in, proceed with file listing
				await handleListFiles();
			}
		} finally {
			if (isMounted) {
				isConfidiosLoading = false;
			}
		}
	}

	async function handleListFiles() {
		// Check if user is logged in before trying to fetch files
		if (!$confidiosStore.currentUserStatus?.isLoggedInConfidios) {
			files = [];
			return;
		}

		// Cancel previous request if exists
		if (currentListRequest) {
			currentListRequest.abort();
		}

		// Create new abort controller and increment counter
		currentListRequest = new AbortController();
		const thisRequestId = ++requestCounter;

		if (isMounted) {
			isLoading = true;
			error = null;
			selectedFile = null; // Clear selection when navigating
		}

		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/fs/ls`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					path: currentPath
				}),
				signal: currentListRequest.signal
			});

			// Check if this is still the latest request
			if (thisRequestId !== requestCounter || !isMounted) {
				return;
			}

			if (!response.ok) {
				const errorData = await response.text();
				throw new Error(errorData);
			}

			const data = await response.json();

			// Double-check before updating state
			if (thisRequestId === requestCounter && isMounted) {
				// Filter out current and parent directory entries
				files = data.files.filter((f: FileItem) => f.path !== '.' && f.path !== '..');
				toast.success($i18n.t('Files retrieved successfully'));
			}
		} catch (err) {
			if (err.name === 'AbortError') {
				// Request was aborted, ignore
				return;
			}

			// Only handle error if this is still the latest request
			if (thisRequestId === requestCounter && isMounted) {
				console.error('Error fetching files:', err);
				error = err.message;
				toast.error($i18n.t('Failed to fetch files. Please try again.'));
			}
		} finally {
			// Only update loading state if this is the latest request
			if (thisRequestId === requestCounter && isMounted) {
				isLoading = false;
			}

			// Clear the controller if it matches the current one
			if (currentListRequest?.signal === currentListRequest?.signal) {
				currentListRequest = null;
			}
		}
	}

	//GET GROUPS
	async function handleGroupFiles() {
		// Cancel any pending list requests
		if (currentListRequest) {
			currentListRequest.abort();
		}

		currentListRequest = new AbortController();
		const thisRequestId = ++requestCounter;

		if (isMounted) {
			isLoading = true;
			error = null;
			selectedFile = null;
			isGroupView = true; // Set group view state
			currentPath = 'home/group'; // Update current path
		}

		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/fs/ls`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					path: 'home/group'
				}),
				signal: currentListRequest.signal
			});

			// Check if this is still the latest request
			if (thisRequestId !== requestCounter || !isMounted) {
				return;
			}

			if (!response.ok) {
				const errorData = await response.text();
				throw new Error(errorData);
			}

			const data = await response.json();

			// Double-check before updating state
			if (thisRequestId === requestCounter && isMounted) {
				// Filter out current and parent directory entries
				files = data.files.filter((f: FileItem) => f.path !== '.' && f.path !== '..');
				toast.success($i18n.t('Files retrieved successfully'));
			}
		} catch (err) {
			if (err.name === 'AbortError') {
				return;
			}

			if (thisRequestId === requestCounter && isMounted) {
				console.error('Error fetching files:', err);
				error = err.message;
				toast.error($i18n.t('Failed to fetch files. Please try again.'));
			}
		} finally {
			if (thisRequestId === requestCounter && isMounted) {
				isLoading = false;
			}

			if (currentListRequest?.signal === currentListRequest?.signal) {
				currentListRequest = null;
			}
		}
	}

	// Create a debounced version with proper cleanup
	const debouncedListFiles = debounce(() => {
		if (isMounted) {
			handleListFiles();
		}
	}, 300);

	// Clear selection when path changes (with mount check)
	$: if (currentPath && isMounted) {
		selectedFile = null;
	}

	function handleItemClick(item: FileItem) {
		if (!isMounted) return;

		if (isFile(item)) {
			// Select file
			selectedFile = selectedFile?.path === item.path ? null : item; // Toggle selection
		} else {
			// Navigate to folder
			selectedFile = null; // Clear selection
			// Use the correct base path based on the view
			if (currentPath === 'home/data' || currentPath.startsWith('home/data/')) {
				currentPath = `${currentPath}/${item.path}`;
			} else if (currentPath === 'home/group' || currentPath.startsWith('home/group/')) {
				currentPath = `${currentPath}/${item.path}`;
			}
			debouncedListFiles();
		}
	}

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
		if (!selectedFile || !isMounted) return;

		// Cancel any pending download
		if (currentDownloadRequest) {
			currentDownloadRequest.abort();
		}

		currentDownloadRequest = new AbortController();
		const fullPath = `${currentPath}/${selectedFile.path}`;

		if (isMounted) {
			isDownloading = true;
		}

		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/fs/cat`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					path: fullPath
				}),
				signal: currentDownloadRequest.signal
			});

			if (!isMounted) return;

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

			if (isMounted) {
				toast.success(`Downloaded: ${selectedFile.path}`);
			}
		} catch (err) {
			if (err.name === 'AbortError') {
				return;
			}

			if (isMounted) {
				console.error('Download error:', err);
				toast.error(`Download failed: ${err.message}`);
			}
		} finally {
			if (isMounted) {
				isDownloading = false;
			}
			currentDownloadRequest = null;
		}
	}

	function handleBackNavigation() {
		if (!isMounted) return;

		const pathParts = currentPath.split('/');
		pathParts.pop();
		currentPath = pathParts.join('/') || 'home/data';
		handleListFiles();
	}

	function openCreateFolderModal() {
		if (!isMounted) return;

		newFolderName = '';
		isCreateFolderModalOpen = true;
	}

	function closeCreateFolderModal() {
		if (!isMounted) return;

		isCreateFolderModalOpen = false;
		newFolderName = '';
	}

	async function handleCreateFolder() {
		if (!newFolderName.trim() || !isMounted) return;

		isCreatingFolder = true;

		// Use AbortController for folder creation too
		const createController = new AbortController();

		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/fs/mkdir`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					path: `${currentPath}/${newFolderName}`
				}),
				signal: createController.signal
			});

			if (!isMounted) return;

			if (!response.ok) {
				const errorData = await response.json();
				// Extract the specific error message from the API response
				const errorMessage = errorData.detail?.[0]?.msg || 'Failed to create folder';
				throw new Error(errorMessage);
			}

			await response.json();

			if (isMounted) {
				toast.success($i18n.t('Folder created successfully'));
				closeCreateFolderModal();
				handleListFiles(); // Refresh the file list
			}
		} catch (err) {
			if (err.name === 'AbortError') {
				return;
			}

			if (isMounted) {
				console.error('Error creating folder:', err);
				toast.error(err.message); // Display the specific error message
			}
		} finally {
			if (isMounted) {
				isCreatingFolder = false;
			}
		}
	}

	let initialMount = true;

	// Reactive statement with mount check
	$: if ($confidiosStore.currentUserStatus?.isLoggedInConfidios && isMounted) {
		if (!initialMount) {
			handleListFiles();
		}
	}

	onMount(() => {
		isMounted = true;
		if ($confidiosStore.currentUserStatus?.isLoggedInConfidios) {
			handleListFiles();
		}
		initialMount = false;

		// Return cleanup function
		return () => {
			isMounted = false;
		};
	});

	onDestroy(() => {
		isMounted = false;

		// Cancel all pending requests
		if (currentListRequest) {
			currentListRequest.abort();
			currentListRequest = null;
		}

		if (currentDownloadRequest) {
			currentDownloadRequest.abort();
			currentDownloadRequest = null;
		}

		// Cancel debounced function
		debouncedListFiles.cancel();

		// Reset request counter
		requestCounter = 0;
	});
</script>

<div class="container mx-auto p-4">
	<div class="bg-white dark:bg-gray-800 rounded-lg shadow">
		<div class="p-4 border-b border-gray-200 dark:border-gray-700">
			<div class="flex items-center gap-1 mb-4">
				<img
					src="/assets/images/confidios_logo.jpeg"
					alt="Confidios logo"
					class="h-7 w-auto object-contain"
				/>
				<h2 class="text-lg font-semibold text-gray-800 dark:text-white">
					{$i18n.t('Secure File Explorer')}
				</h2>
			</div>
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
				<!-- Path display with clickable segments -->
				<p class="text-sm text-gray-600 dark:text-gray-400 flex items-center gap-1">
					{#if isGroupView}
						{#if currentPath === 'home/group'}
							<span>home/group</span>
						{:else}
							<button
								class="hover:text-blue-500 hover:underline"
								on:click={() => {
									currentPath = 'home/group';
									handleListFiles();
								}}
							>
								home/group
							</button>
							{#each currentPath.slice(10).split('/').filter(Boolean) as segment, idx}
								<span class="text-gray-400">/</span>
								{#if idx === currentPath.slice(10).split('/').filter(Boolean).length - 1}
									<!-- Last segment is not clickable -->
									<span>{segment}</span>
								{:else}
									<!-- Make intermediate segments clickable -->
									<button
										class="hover:text-blue-500 hover:underline"
										on:click={() => {
											const segments = currentPath.slice(10).split('/').filter(Boolean);
											const newPath = 'home/group/' + segments.slice(0, idx + 1).join('/');
											currentPath = newPath;
											handleListFiles();
										}}
									>
										{segment}
									</button>
								{/if}
							{/each}
						{/if}
					{:else if currentPath === 'home/data'}
						<span>home/data</span>
					{:else}
						<button
							class="hover:text-blue-500 hover:underline"
							on:click={() => {
								currentPath = 'home/data';
								handleListFiles();
							}}
						>
							home/data
						</button>
						{#each currentPath.slice(9).split('/').filter(Boolean) as segment, idx}
							<span class="text-gray-400">/</span>
							{#if idx === currentPath.slice(9).split('/').filter(Boolean).length - 1}
								<!-- Last segment is not clickable -->
								<span>{segment}</span>
							{:else}
								<!-- Make intermediate segments clickable -->
								<button
									class="hover:text-blue-500 hover:underline"
									on:click={() => {
										const segments = currentPath.slice(9).split('/').filter(Boolean);
										const newPath = 'home/data/' + segments.slice(0, idx + 1).join('/');
										currentPath = newPath;
										handleListFiles();
									}}
								>
									{segment}
								</button>
							{/if}
						{/each}
					{/if}
				</p>
			</div>
		</div>

		<div class="p-4">
			<!-- Update the main action buttons -->
			<div class="flex items-center justify-between gap-4 mb-4">
				<!-- Only show action buttons if logged in -->
				{#if $confidiosStore.currentUserStatus?.isLoggedInConfidios}
					<!-- Left side buttons -->
					<div class="flex items-center gap-4">
						<!-- Home button -->
						<Tooltip content={$i18n.t('Home')}>
							<button
								on:click={() => {
									isGroupView = false;
									currentPath = 'home/data';
									handleListFiles();
								}}
								class="p-2 border border-gray-500 text-gray-500 rounded hover:bg-gray-500 hover:text-white transition"
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
										d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"
									/>
								</svg>
							</button>
						</Tooltip>

						<!-- Refresh button -->
						<Tooltip content={isLoading ? $i18n.t('Loading...') : $i18n.t('Refresh')}>
							<button
								on:click={handleListFiles}
								disabled={isLoading}
								class="p-2 border border-blue-500 text-blue-500 rounded hover:bg-blue-500 hover:text-white transition disabled:opacity-50"
							>
								{#if isLoading}
									<svg
										class="animate-spin h-5 w-5"
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
										class="h-5 w-5"
										fill="none"
										viewBox="0 0 24 24"
										stroke="currentColor"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
										/>
									</svg>
								{/if}
							</button>
						</Tooltip>

						<!-- Download button - only show when a file is selected -->
						<Tooltip content={isDownloading ? $i18n.t('Downloading...') : $i18n.t('Download')}>
							<button
								on:click={handleDownload}
								disabled={!selectedFile || isDownloading || !isFile(selectedFile)}
								class="p-2 border border-green-500 text-green-500 rounded hover:bg-green-500 hover:text-white transition disabled:opacity-50"
							>
								{#if isDownloading}
									<svg
										class="animate-spin h-5 w-5"
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
										class="h-5 w-5"
										fill="none"
										viewBox="0 0 24 24"
										stroke="currentColor"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
										/>
									</svg>
								{/if}
							</button>
						</Tooltip>

						<!-- New Folder button -->
						<!-- New Folder button -->
						<Tooltip content={$i18n.t('New Secure Folder')}>
							<button
								on:click={openCreateFolderModal}
								class="p-2 border border-orange-500 text-orange-500 rounded hover:bg-orange-500 hover:text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
								disabled={isLoading || isCreatingFolder}
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
							</button>
						</Tooltip>
					</div>

					<!-- Right-aligned group button -->
					<div>
						<Tooltip content={$i18n.t('Groups')}>
							<button
								on:click={handleGroupFiles}
								class="p-2 border border-blue-500 rounded transition
				{isGroupView || currentPath.startsWith('home/group')
									? 'bg-blue-500 text-white hover:bg-blue-600'
									: 'text-blue-500 hover:bg-blue-500 hover:text-white'}"
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
										d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"
									/>
								</svg>
							</button>
						</Tooltip>
					</div>
				{/if}
			</div>

			<FileList
				{files}
				{selectedFile}
				{isLoading}
				{error}
				on:select={({ detail }) => handleItemClick(detail.item)}
			/>
		</div>
	</div>
</div>

<!-- Add the modal -->
<CreateFolderModal
	isOpen={isCreateFolderModalOpen}
	isCreating={isCreatingFolder}
	bind:folderName={newFolderName}
	on:close={closeCreateFolderModal}
	on:create={({ detail }) => handleCreateFolder()}
/>
