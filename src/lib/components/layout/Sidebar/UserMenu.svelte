<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { createEventDispatcher, getContext, onMount } from 'svelte';

	import { flyAndScale } from '$lib/utils/transitions';
	import { goto } from '$app/navigation';
	import { fade, slide } from 'svelte/transition';

	import { getUsage } from '$lib/apis';
	import { userSignOut } from '$lib/apis/auths';

	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import { showSettings, mobile, showSidebar, user } from '$lib/stores';
	import { confidiosStore } from '$lib/stores/integrations';

	import { toast } from 'svelte-sonner';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ArchiveBox from '$lib/components/icons/ArchiveBox.svelte';
	import QuestionMarkCircle from '$lib/components/icons/QuestionMarkCircle.svelte';
	import Map from '$lib/components/icons/Map.svelte';
	import Keyboard from '$lib/components/icons/Keyboard.svelte';
	import ShortcutsModal from '$lib/components/chat/ShortcutsModal.svelte';
	import Settings from '$lib/components/icons/Settings.svelte';
	import Code from '$lib/components/icons/Code.svelte';
	import UserGroup from '$lib/components/icons/UserGroup.svelte';
	import SignOut from '$lib/components/icons/SignOut.svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let role = '';
	export let help = false;
	export let className = 'max-w-[240px]';

	let showShortcuts = false;

	const dispatch = createEventDispatcher();

	let usage = null;
	const getUsageInfo = async () => {
		const res = await getUsage(localStorage.token).catch((error) => {
			console.error('Error fetching usage info:', error);
		});

		if (res) {
			usage = res;
		} else {
			usage = null;
		}
	};

	$: if (show) {
		getUsageInfo();
	}

	async function getMyConfidiosStatus() {
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/users/me`, {
				headers: {
					Authorization: `Bearer ${localStorage.token}`
				}
			});

			if (!response.ok) {
				const errorText = await response.text();

				throw new Error(`Failed to get Confidios status: ${response.status}`);
			}

			const data = await response.json();

			// Update the Confidios store with the user's status
			const newStatus = {
				isConfidiosUser: data.is_confidios_user,
				isLoggedInConfidios: data.is_logged_in,
				username: data.confidios_username,
				balance: data.balance
			};

			confidiosStore.update((state) => {
				const newState = {
					...state,
					currentUserStatus: newStatus
				};

				return newState;
			});

			return data;
		} catch (error) {
			// Reset user status on error
			confidiosStore.update((state) => ({
				...state,
				currentUserStatus: undefined
			}));
			return null;
		}
	}

	onMount(async () => {
		await getMyConfidiosStatus();
	});

	// Add loading state variable
	let isConfidiosLoading = false;

	async function handleConfidiosLogin() {
		try {
			isConfidiosLoading = true;
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/auths/user/login`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${localStorage.token}`
				},
				body: JSON.stringify({
					confidios_username: $user.email.replace('@', '-at-').toLowerCase()
				})
			});

			if (!response.ok) {
				throw new Error('Failed to login to Confidios');
			}

			const data = await response.json();
			console.log('Confidios login response:', data);

			// Show success toast
			toast.success(data.status || $i18n.t('Successfully logged in to Confidios'));

			confidiosStore.update((state) => ({
				...state,
				currentUserStatus: {
					isConfidiosUser: true,
					isLoggedInConfidios: true,
					username: data.confidios_user,
					balance: data.confidios_balance
				}
			}));
		} catch (error) {
			console.error('Error logging into Confidios:', error);
			// Show error toast
			toast.error($i18n.t('Failed to login to Confidios. Please try again.'));

			confidiosStore.update((state) => ({
				...state,
				currentUserStatus: {
					...state.currentUserStatus,
					isLoggedInConfidios: false
				}
			}));
		} finally {
			isConfidiosLoading = false;
		}
	}

	async function handleConfidiosLogout() {
		try {
			isConfidiosLoading = true;
			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/auths/user/logout`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`
				}
			});

			if (!response.ok) {
				throw new Error('Failed to logout from Confidios');
			}

			const data = await response.json();
			console.log('Confidios logout response:', data);

			// Show success toast
			toast.success(data.status || $i18n.t('Successfully logged out from Confidios'));

			confidiosStore.update((state) => ({
				...state,
				currentUserStatus: {
					...state.currentUserStatus,
					isLoggedInConfidios: false,
					balance: undefined
				}
			}));
		} catch (error) {
			console.error('Error logging out from Confidios:', error);
			// Show error toast
			toast.error($i18n.t('Failed to logout from Confidios. Please try again.'));
		} finally {
			isConfidiosLoading = false;
		}
	}
</script>

<ShortcutsModal bind:show={showShortcuts} />

<!-- svelte-ignore a11y-no-static-element-interactions -->
<DropdownMenu.Root
	bind:open={show}
	onOpenChange={(state) => {
		dispatch('change', state);
	}}
>
	<DropdownMenu.Trigger>
		<slot />
	</DropdownMenu.Trigger>

	<slot name="content">
		<DropdownMenu.Content
			class="w-full {className} text-sm rounded-xl px-1 py-1.5 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg font-primary"
			sideOffset={4}
			side="bottom"
			align="start"
			transition={(e) => fade(e, { duration: 100 })}
		>
			<button
				class="flex rounded-md py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition"
				on:click={async () => {
					await showSettings.set(true);
					show = false;

					if ($mobile) {
						showSidebar.set(false);
					}
				}}
			>
				<div class=" self-center mr-3">
					<Settings className="w-5 h-5" strokeWidth="1.5" />
				</div>
				<div class=" self-center truncate">{$i18n.t('Settings')}</div>
			</button>

			<button
				class="flex rounded-md py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition"
				on:click={() => {
					dispatch('show', 'archived-chat');
					show = false;

					if ($mobile) {
						showSidebar.set(false);
					}
				}}
			>
				<div class=" self-center mr-3">
					<ArchiveBox className="size-5" strokeWidth="1.5" />
				</div>
				<div class=" self-center truncate">{$i18n.t('Archived Chats')}</div>
			</button>

			{#if role === 'admin'}
				<button
					class="flex rounded-md py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition"
					on:click={() => {
						goto('/playground');
						show = false;

						if ($mobile) {
							showSidebar.set(false);
						}
					}}
				>
					<div class=" self-center mr-3">
						<Code className="size-5" strokeWidth="1.5" />
					</div>
					<div class=" self-center truncate">{$i18n.t('Playground')}</div>
				</button>

				<button
					class="flex rounded-md py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition"
					on:click={() => {
						goto('/admin');
						show = false;

						if ($mobile) {
							showSidebar.set(false);
						}
					}}
				>
					<div class=" self-center mr-3">
						<UserGroup className="w-5 h-5" strokeWidth="1.5" />
					</div>
					<div class=" self-center truncate">{$i18n.t('Admin Panel')}</div>
				</button>
			{/if}

			{#if help}
				<hr class=" border-gray-100 dark:border-gray-800 my-1 p-0" />

				<!-- {$i18n.t('Help')} -->
				<DropdownMenu.Item
					class="flex gap-2 items-center py-1.5 px-3 text-sm select-none w-full cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md transition"
					id="chat-share-button"
					on:click={() => {
						window.open('https://docs.openwebui.com', '_blank');
						show = false;
					}}
				>
					<QuestionMarkCircle className="size-5" />
					<div class="flex items-center">{$i18n.t('Documentation')}</div>
				</DropdownMenu.Item>

				<!-- Releases -->
				<DropdownMenu.Item
					class="flex gap-2 items-center py-1.5 px-3 text-sm select-none w-full cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md transition"
					id="menu-item-releases"
					on:click={() => {
						window.open('https://github.com/open-webui/open-webui/releases', '_blank');
						show = false;
					}}
				>
					<Map className="size-5" />
					<div class="flex items-center">{$i18n.t('Releases')}</div>
				</DropdownMenu.Item>

				<DropdownMenu.Item
					class="flex gap-2 items-center py-1.5 px-3 text-sm select-none w-full cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md transition"
					id="chat-share-button"
					on:click={() => {
						showShortcuts = !showShortcuts;
						show = false;
					}}
				>
					<Keyboard className="size-5" />
					<div class="flex items-center">{$i18n.t('Keyboard shortcuts')}</div>
				</DropdownMenu.Item>
			{/if}

			{#if $confidiosStore.currentUserStatus?.isConfidiosUser && role !== 'admin'}
				<hr class="border-gray-100 dark:border-gray-800 my-1 p-0" />
				<button
					class="flex rounded-md py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition"
					on:click={$confidiosStore.currentUserStatus?.isLoggedInConfidios
						? handleConfidiosLogout
						: handleConfidiosLogin}
					disabled={isConfidiosLoading}
				>
					<div class="self-center mr-3">
						{#if isConfidiosLoading}
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
							<Code className="w-5 h-5" />
						{/if}
					</div>
					<div class="self-center truncate">
						{#if isConfidiosLoading}
							{$i18n.t(
								$confidiosStore.currentUserStatus?.isLoggedInConfidios
									? 'Logging out...'
									: 'Logging in...'
							)}
						{:else}
							{$i18n.t(
								$confidiosStore.currentUserStatus?.isLoggedInConfidios
									? 'Logout from Confidios'
									: 'Login to Confidios'
							)}
						{/if}
					</div>
				</button>
			{/if}

			<hr class=" border-gray-100 dark:border-gray-800 my-1 p-0" />

			<button
				class="flex rounded-md py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition"
				on:click={async () => {
					const res = await userSignOut();
					user.set(null);
					localStorage.removeItem('token');

					location.href = res?.redirect_url ?? '/auth';
					show = false;
				}}
			>
				<div class=" self-center mr-3">
					<SignOut className="w-5 h-5" />
				</div>
				<div class=" self-center truncate">{$i18n.t('Sign Out')}</div>
			</button>

			{#if usage}
				{#if usage?.user_ids?.length > 0}
					<hr class=" border-gray-100 dark:border-gray-800 my-1 p-0" />

					<Tooltip
						content={usage?.model_ids && usage?.model_ids.length > 0
							? `${$i18n.t('Running')}: ${usage.model_ids.join(', ')} ✨`
							: ''}
					>
						<div
							class="flex rounded-md py-1 px-3 text-xs gap-2.5 items-center"
							on:mouseenter={() => {
								getUsageInfo();
							}}
						>
							<div class=" flex items-center">
								<span class="relative flex size-2">
									<span
										class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"
									/>
									<span class="relative inline-flex rounded-full size-2 bg-green-500" />
								</span>
							</div>

							<div class=" ">
								<span class="">
									{$i18n.t('Active Users')}:
								</span>
								<span class=" font-semibold">
									{usage?.user_ids?.length}
								</span>
							</div>
						</div>
					</Tooltip>
				{/if}
			{/if}
		</DropdownMenu.Content>
	</slot>
</DropdownMenu.Root>
