<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import ConfidiosUsersTable from '$lib/components/tables/ConfidiosUsersTable.svelte';
	import { confidiosStore } from '$lib/stores/integrations';

	const i18n = getContext('i18n');

	let isLoading = false;
	let isLoggedIn = false;
	let users = null;
	let total = null;
	let page = 1;

	type ConfidiosStatus = {
		confidios_user: string;
		confidios_session_id: string;
		is_logged_in: boolean;
		balance?: string;
	} | null;

	let confidiosStatus: ConfidiosStatus = null;
	let isLoadingStatus = false;
	let hasCheckedInitialStatus = false;

	// Check status when page loads
	onMount(async () => {
		await checkConfidiosStatus();
	});

	async function checkConfidiosStatus() {
		try {
			isLoadingStatus = true;
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/status`, {
				headers: {
					Authorization: `Bearer ${localStorage.token}`
				}
			});

			if (response.ok) {
				// User is logged in
				confidiosStatus = await response.json();
				isLoggedIn = confidiosStatus?.is_logged_in ?? false;

				confidiosStore.update((state) => ({
					...state,
					isAdminLoggedIn: isLoggedIn
				}));
			} else if (response.status === 401) {
				// User is not logged in - this is expected
				isLoggedIn = false;
				confidiosStatus = null;
				confidiosStore.update((state) => ({
					...state,
					isAdminLoggedIn: false
				}));
			} else {
				// Other error
				throw new Error('Failed to get status');
			}
		} catch (error) {
			// Only show error if it's not a 401 (not logged in)
			console.log('Status check error:', error);
			isLoggedIn = false;
			confidiosStatus = null;
		} finally {
			isLoadingStatus = false;
			hasCheckedInitialStatus = true;
		}
	}

	async function handleConfidiosLogin() {
		try {
			isLoading = true;
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/auths/login`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${localStorage.token}`
				}
			});

			if (!response.ok) {
				throw new Error('Failed to login');
			}

			const data = await response.json();
			toast.success(data.status);
			isLoggedIn = data.confidios_is_logged_in;

			// Refresh status after successful login
			await checkConfidiosStatus();

			confidiosStore.update((state) => ({
				...state,
				isAdminLoggedIn: true
			}));
		} catch (error) {
			toast.error($i18n.t('Failed to login to Confidios. Please try again.'));
		} finally {
			isLoading = false;
		}
	}

	async function handleConfidiosLogout() {
		try {
			isLoading = true;
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/auths/logout`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${localStorage.token}`
				}
			});

			if (!response.ok) {
				throw new Error('Failed to logout');
			}

			const data = await response.json();
			toast.success(data.status);
			isLoggedIn = false;
			confidiosStatus = null;

			confidiosStore.update((state) => ({
				...state,
				isAdminLoggedIn: false
			}));

			console.log('Logout clicked');
		} catch (error) {
			toast.error($i18n.t('Failed to logout from Confidios. Please try again.'));
		} finally {
			isLoading = false;
		}
	}

	// Manual status refresh function
	async function handleGetStatus() {
		await checkConfidiosStatus();
	}
</script>

<div class="flex flex-col gap-4">
	<div class="flex items-center justify-between">
		<h1 class="text-2xl font-bold">
			{$i18n.t('Integrations')}
		</h1>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
		<!-- Confidios Integration Card -->
		<div class="p-4 rounded-lg border border-gray-200 dark:border-gray-700">
			<h2 class="text-xl font-semibold mb-2">Confidios</h2>
			<p class="text-gray-600 dark:text-gray-400 mb-4">
				Secure feature management and access control
			</p>

			<!-- Show loading state on initial load -->
			{#if !hasCheckedInitialStatus}
				<div class="flex items-center justify-center py-8">
					<svg
						class="spinner w-6 h-6 text-gray-400"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
					>
						<circle cx="12" cy="12" r="10" stroke="currentColor" fill="none"></circle>
						<path
							d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
							fill="currentColor"
						></path>
					</svg>
					<span class="ml-2 text-gray-500">Checking status...</span>
				</div>
			{:else}
				<div class="flex items-center justify-between">
					<div class="flex items-center gap-2">
						{#if isLoggedIn}
							<svg class="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20">
								<path
									d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
								/>
							</svg>
							<span class="text-sm text-green-600 dark:text-green-400">Logged in</span>
						{:else}
							<svg class="w-4 h-4 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
								<path
									fill-rule="evenodd"
									d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
									clip-rule="evenodd"
								/>
							</svg>
							<span class="text-sm text-gray-400">Not logged in</span>
						{/if}
					</div>
					<button
						class="px-4 py-2 {isLoggedIn
							? 'bg-red-500 hover:bg-red-600'
							: 'bg-blue-500 hover:bg-blue-600'} text-white rounded disabled:opacity-50 flex items-center gap-2"
						on:click={isLoggedIn ? handleConfidiosLogout : handleConfidiosLogin}
						disabled={isLoading}
					>
						{#if isLoading}
							<svg
								class="spinner w-4 h-4"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="2"
							>
								<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" fill="none"
								></circle>
								<path
									class="opacity-75"
									d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
									fill="currentColor"
								></path>
							</svg>
						{/if}
						{isLoading ? 'Loading...' : isLoggedIn ? 'Log out' : 'Admin Login'}
					</button>
				</div>

				<!-- Status Section - only show if logged in -->
				{#if isLoggedIn}
					<div class="mt-4 border-t pt-4">
						<button
							class="w-full px-4 py-2 bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200 rounded hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 flex items-center justify-center gap-2"
							on:click={handleGetStatus}
							disabled={isLoadingStatus}
						>
							{#if isLoadingStatus}
								<svg
									class="spinner w-4 h-4"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="2"
								>
									<circle cx="12" cy="12" r="10" stroke="currentColor" fill="none"></circle>
									<path
										d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
										fill="currentColor"
									></path>
								</svg>
							{/if}
							{isLoadingStatus ? 'Loading...' : 'Refresh Status'}
						</button>

						{#if confidiosStatus}
							<div class="mt-4 p-4 bg-gray-50 dark:bg-gray-800 rounded">
								<h3 class="text-sm font-semibold mb-2">Status Information</h3>
								<div class="space-y-2 text-sm">
									<p>User: {confidiosStatus.confidios_user}</p>
									<p>Session ID: {confidiosStatus.confidios_session_id}</p>
									{#if confidiosStatus.balance}
										<p>Balance: {confidiosStatus.balance}</p>
									{/if}
									<p class="flex items-center gap-2">
										Status:
										{#if confidiosStatus.is_logged_in}
											<span class="text-green-500">Active</span>
										{:else}
											<span class="text-red-500">Inactive</span>
										{/if}
									</p>
								</div>
							</div>
						{/if}
					</div>
				{/if}
			{/if}
		</div>
	</div>

	<!-- Show users table only if logged in -->
	{#if isLoggedIn === true}
		<div class="p-4 rounded-lg border border-gray-200 dark:border-gray-700">
			<ConfidiosUsersTable bind:users bind:total bind:page />
		</div>
	{/if}
</div>

<style>
	.spinner {
		animation: spin 1s linear infinite;
	}

	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
</style>
