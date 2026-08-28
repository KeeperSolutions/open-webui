<script lang="ts">
	import { createEventDispatcher, getContext, onMount, tick } from 'svelte';

	import { goto } from '$app/navigation';
	import { fade, slide } from 'svelte/transition';

	import { getUsage } from '$lib/apis';
	import { getSessionUser, userSignOut } from '$lib/apis/auths';

<<<<<<< HEAD
	import {
		showSettings,
		mobile,
		showSidebar,
		showShortcuts,
		user,
		config,
		settings
	} from '$lib/stores';
=======
	import { showSettings, mobile, showSidebar, user, config, settings } from '$lib/stores';
>>>>>>> v0.11.0

	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UserStatusModal from './UserStatusModal.svelte';
	import Emoji from '$lib/components/common/Emoji.svelte';
<<<<<<< HEAD
	import XMark from '$lib/components/icons/XMark.svelte';
	import Note from '$lib/components/icons/Note.svelte';
	import Pin from '$lib/components/icons/Pin.svelte';
	import PinSlash from '$lib/components/icons/PinSlash.svelte';
=======
	import CalendarIcon from './icons/Calendar.svelte';
	import ClockIcon from './icons/Clock.svelte';
	import CodeIcon from './icons/Code.svelte';
	import EmojiFaceIcon from './icons/EmojiFace.svelte';
	import HelpCircleIcon from './icons/HelpCircle.svelte';
	import LogOutIcon from './icons/LogOut.svelte';
	import MapIcon from './icons/Map.svelte';
	import NotesIcon from './icons/Notes.svelte';
	import PinIcon from './icons/Pin.svelte';
	import PinSlashIcon from './icons/PinSlash.svelte';
	import Settings from '$lib/components/icons/Settings.svelte';
	import KeyIcon from './icons/Key.svelte';
	import UserIcon from './icons/User.svelte';
	import WorkspaceIcon from './icons/Workspace.svelte';
	import XMarkIcon from './icons/XMark.svelte';
>>>>>>> v0.11.0
	import { updateUserStatus, updateUserSettings } from '$lib/apis/users';
	import { toast } from 'svelte-sonner';
	import CreditCard from '$lib/components/icons/CreditCard.svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let role = '';

	export let profile = false;
	export let help = false;

	export let className = 'w-[240px]';
	export let align = 'end';

	export let showActiveUsers = true;

	let showUserStatusModal = false;
	let shiftKey = false;

	const dispatch = createEventDispatcher();

	const DEFAULT_PINNED_ITEMS = ['notes', 'workspace'];

	$: pinnedItems = $settings?.pinnedMenuItems ?? DEFAULT_PINNED_ITEMS;

	const isPinned = (id: string) => {
		return pinnedItems.includes(id);
	};

	const togglePin = async (id: string) => {
		let updated;
		if (isPinned(id)) {
			updated = pinnedItems.filter((item) => item !== id);
		} else {
			updated = [...pinnedItems, id];
		}
		await settings.set({ ...$settings, pinnedMenuItems: updated });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};

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

	const handleDropdownChange = (state) => {
		dispatch('change', state);

		// Fetch usage info when dropdown opens, if user has permission
		if (state && ($config?.features?.enable_public_active_users_count || role === 'admin')) {
			getUsageInfo();
		}
	};
</script>

<svelte:window
	on:keydown={(e) => {
		if (e.key === 'Shift') shiftKey = true;
	}}
	on:keyup={(e) => {
		if (e.key === 'Shift') shiftKey = false;
	}}
/>

<<<<<<< HEAD
<ShortcutsModal bind:show={$showShortcuts} />
=======
>>>>>>> v0.11.0
<UserStatusModal
	bind:show={showUserStatusModal}
	onSave={async () => {
		user.set(await getSessionUser(localStorage.token));
	}}
/>

<Dropdown bind:show onOpenChange={handleDropdownChange} {align}>
	<slot />

	<div slot="content">
		<DropdownMenu className="{className} font-sans text-xs">
			{#if $user}
				<div>
					<button
						class="flex h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-xs w-full hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none text-left"
						type="button"
						on:click={async () => {
							show = false;
							await showSettings.set('account');

							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center shrink-0 size-4.5 flex items-center justify-center">
							<img
								src={`${WEBUI_API_BASE_URL}/users/${$user.id}/profile/image`}
								alt=""
								class="size-4.5 rounded-full object-cover"
							/>
						</div>
						<div class="self-center min-w-0 flex-1 truncate">{$user.name}</div>

<<<<<<< HEAD
						<div class=" flex items-center gap-2">
							{#if $user?.is_active ?? true}
								<div>
									<span class="relative flex size-2">
										<span
											class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"
										></span>
										<span class="relative inline-flex rounded-full size-2 bg-green-500"></span>
									</span>
								</div>

								<span class="text-xs"> {$i18n.t('Active')} </span>
							{:else}
								<div>
									<span class="relative flex size-2">
										<span class="relative inline-flex rounded-full size-2 bg-gray-500"></span>
									</span>
								</div>

								<span class="text-xs"> {$i18n.t('Away')} </span>
							{/if}
						</div>
					</div>
=======
						{#if showActiveUsers && ($config?.features?.enable_public_active_users_count || role === 'admin') && usage?.user_count}
							<Tooltip
								content={usage?.model_ids && usage?.model_ids.length > 0
									? `${$i18n.t('Running')}: ${usage.model_ids.join(', ')} ✨`
									: $i18n.t('Active Users')}
							>
								<div
									class="ml-auto flex shrink-0 items-center justify-end gap-1 rounded-full px-1.5 py-0.5 text-[11px] leading-none text-gray-500 dark:text-gray-400"
									on:mouseenter={() => {
										if ($config?.features?.enable_public_active_users_count || role === 'admin') {
											getUsageInfo();
										}
									}}
								>
									<span class="size-1.5 rounded-full bg-green-500" />
									<span>{usage.user_count}</span>
								</div>
							</Tooltip>
						{/if}
					</button>
>>>>>>> v0.11.0
				</div>
			{/if}

			{#if profile}
				{#if $user?.status_emoji || $user?.status_message}
					<div class="user-menu-status">
						<button
							class="w-full h-[1.6875rem] gap-2 rounded-xl px-2 hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none text-xs flex items-center text-left"
							type="button"
							on:click={() => {
								show = false;
								showUserStatusModal = true;
							}}
						>
							{#if $user?.status_emoji}
								<div class="self-center shrink-0 size-4.5 flex items-center justify-center">
									<Emoji className="size-3.5" shortCode={$user?.status_emoji} />
								</div>
							{/if}

							<Tooltip
								content={$user?.status_message}
								className="self-center line-clamp-2 flex-1 text-left"
							>
								{$user?.status_message}
							</Tooltip>

							<div class="self-center">
								<Tooltip content={$i18n.t('Clear status')}>
									<button
										class="flex size-5 items-center justify-center"
										type="button"
										on:click={async (e) => {
											e.preventDefault();
											e.stopPropagation();
											e.stopImmediatePropagation();

											const res = await updateUserStatus(localStorage.token, {
												status_emoji: '',
												status_message: ''
											});

											if (res) {
												toast.success($i18n.t('Status cleared successfully'));
												user.set(await getSessionUser(localStorage.token));
											} else {
												toast.error($i18n.t('Failed to clear status'));
											}
										}}
									>
										<XMarkIcon className="size-3.5 opacity-50" strokeWidth="1.5" />
									</button>
								</Tooltip>
							</div>
						</button>
					</div>
				{:else}
					<div class="user-menu-status">
						<button
							class="w-full h-[1.6875rem] gap-2 rounded-xl px-2 hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none text-xs flex items-center text-left"
							type="button"
							on:click={() => {
								show = false;
								showUserStatusModal = true;
							}}
						>
							<div class="self-center shrink-0 size-4.5 flex items-center justify-center">
								<EmojiFaceIcon className="size-3.5" strokeWidth="1.5" />
							</div>
							<div class="self-center truncate">{$i18n.t('Update your status')}</div>
						</button>
					</div>
				{/if}
			{/if}

			{#if profile}
				<hr class="border-gray-50/30 dark:border-gray-800/30 my-0.5 mx-1 p-0" />
			{/if}

			{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools || $user?.permissions?.workspace?.skills}
				<div class="flex items-center w-full">
					<a
						href="/workspace"
						draggable="false"
						class="flex flex-1 h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/workspace');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center">
							<WorkspaceIcon className="size-3.5" strokeWidth="1.5" />
						</div>
						<div class="self-center truncate">{$i18n.t('Workspace')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('workspace')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100/60 dark:hover:bg-gray-700/60 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('workspace')}
							>
								{#if isPinned('workspace')}
									<PinSlashIcon className="size-3.5" strokeWidth="1.5" />
								{:else}
									<PinIcon className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

<<<<<<< HEAD
			{#if role === 'admin'}
=======
			{#if ($config?.features?.enable_notes ?? false) && ($user?.role === 'admin' || ($user?.permissions?.features?.notes ?? true))}
				<div class="flex items-center w-full">
					<a
						href="/notes"
						draggable="false"
						class="flex flex-1 h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/notes');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center">
							<NotesIcon className="size-3.5" strokeWidth="1.5" />
						</div>
						<div class="self-center truncate">{$i18n.t('Notes')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('notes')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100/60 dark:hover:bg-gray-700/60 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('notes')}
							>
								{#if isPinned('notes')}
									<PinSlashIcon className="size-3.5" strokeWidth="1.5" />
								{:else}
									<PinIcon className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

			{#if $config?.features?.enable_calendar && ($user?.role === 'admin' || $user?.permissions?.features?.calendar)}
				<div class="flex items-center w-full">
					<a
						href="/calendar"
						draggable="false"
						class="flex flex-1 h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/calendar');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center">
							<CalendarIcon className="size-3.5" strokeWidth="1.5" />
						</div>
						<div class="self-center truncate">{$i18n.t('Calendar')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('calendar')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100/60 dark:hover:bg-gray-700/60 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('calendar')}
							>
								{#if isPinned('calendar')}
									<PinSlashIcon className="size-3.5" strokeWidth="1.5" />
								{:else}
									<PinIcon className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

			{#if $config?.features?.enable_automations && ($user?.role === 'admin' || $user?.permissions?.features?.automations)}
				<div class="flex items-center w-full">
					<a
						href="/automations"
						draggable="false"
						class="flex flex-1 h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/automations');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center">
							<ClockIcon className="size-3.5" strokeWidth="1.5" />
						</div>
						<div class="self-center truncate">{$i18n.t('Automations')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('automations')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100/60 dark:hover:bg-gray-700/60 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('automations')}
							>
								{#if isPinned('automations')}
									<PinSlashIcon className="size-3.5" strokeWidth="1.5" />
								{:else}
									<PinIcon className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

			{#if role === 'admin'}
				<div class="flex items-center w-full">
					<a
						href="/playground"
						draggable="false"
						class="flex flex-1 h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/playground');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center">
							<CodeIcon className="size-3.5" strokeWidth="1.5" />
						</div>
						<div class="self-center truncate">{$i18n.t('Playground')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('playground')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100/60 dark:hover:bg-gray-700/60 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('playground')}
							>
								{#if isPinned('playground')}
									<PinSlashIcon className="size-3.5" strokeWidth="1.5" />
								{:else}
									<PinIcon className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

			{#if help}
				<hr class="border-gray-50/30 dark:border-gray-800/30 my-0.5 mx-1 p-0" />

				<!-- {$i18n.t('Help')} -->

				{#if $user?.role === 'admin'}
					<a
						href="https://docs.openwebui.com"
						target="_blank"
						draggable="false"
						class="flex h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] w-full hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
						id="chat-share-button"
						on:click={() => {
							show = false;
						}}
					>
						<div class="self-center">
							<HelpCircleIcon className="size-3.5" />
						</div>
						<div class=" self-center truncate">{$i18n.t('Documentation')}</div>
					</a>

					<!-- Releases -->
					<a
						href="https://github.com/open-webui/open-webui/releases"
						target="_blank"
						draggable="false"
						class="flex h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] w-full hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
						id="chat-share-button"
						on:click={() => {
							show = false;
						}}
					>
						<div class="self-center">
							<MapIcon className="size-3.5" />
						</div>
						<div class=" self-center truncate">{$i18n.t('Releases')}</div>
					</a>
				{/if}

				<button
					class="flex h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] w-full hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
					type="button"
					id="chat-share-button"
					on:click={async () => {
						show = false;
						showSettings.set('shortcuts');

						if ($mobile) {
							await tick();
							showSidebar.set(false);
						}
					}}
				>
					<div class="self-center">
						<KeyIcon className="size-3.5" />
					</div>
					<div class=" self-center truncate">{$i18n.t('Keyboard')}</div>
				</button>
			{/if}

			<hr class="border-gray-50/30 dark:border-gray-800/30 my-0.5 mx-1 p-0" />

			{#if role === 'admin'}
>>>>>>> v0.11.0
				<a
					href="/admin"
					draggable="false"
					class="flex h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] w-full hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
					on:click={async (e) => {
						if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) {
							return;
						}
						e.preventDefault();
						show = false;
						goto('/admin');
						if ($mobile) {
							await tick();
							showSidebar.set(false);
						}
					}}
				>
					<div class="self-center">
						<UserIcon className="size-3.5" strokeWidth="1.5" />
					</div>
					<div class=" self-center truncate">{$i18n.t('Admin Panel')}</div>
				</a>
			{/if}

			<button
<<<<<<< HEAD
				class="flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
				type="button"
				on:click={async () => {
					show = false;

					dispatch('show', 'archived-chat');

					if ($mobile) {
						await tick();

						showSidebar.set(false);
					}
				}}
			>
				<div class=" self-center mr-3">
					<ArchiveBox className="size-5" strokeWidth="1.5" />
				</div>
				<div class=" self-center truncate">{$i18n.t('Archived Chats')}</div>
			</button>

			<hr class=" border-gray-50/30 dark:border-gray-800/30 my-1 p-0" />

			{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools}
				<div class="flex items-center w-full">
					<a
						href="/workspace"
						draggable="false"
						class="flex flex-1 rounded-xl py-1.5 px-3 hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/workspace');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center mr-3">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 0 0 2.25-2.25V6a2.25 2.25 0 0 0-2.25-2.25H6A2.25 2.25 0 0 0 3.75 6v2.25A2.25 2.25 0 0 0 6 10.5Zm0 9.75h2.25A2.25 2.25 0 0 0 10.5 18v-2.25a2.25 2.25 0 0 0-2.25-2.25H6a2.25 2.25 0 0 0-2.25 2.25V18A2.25 2.25 0 0 0 6 20.25Zm9.75-9.75H18a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 18 3.75h-2.25A2.25 2.25 0 0 0 13.5 6v2.25a2.25 2.25 0 0 0 2.25 2.25Z"
								/>
							</svg>
						</div>
						<div class="self-center truncate">{$i18n.t('Workspace')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('workspace')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('workspace')}
							>
								{#if isPinned('workspace')}
									<PinSlash className="size-3.5" strokeWidth="1.5" />
								{:else}
									<Pin className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

			{#if $config?.features?.enable_billing ?? false}
				<button
					class="flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
					type="button"
					on:click={async () => {
						show = false;
						if ($mobile) {
							await tick();
							showSidebar.set(false);
						}
						goto('/billing');
					}}
				>
					<div class=" self-center mr-3">
						<CreditCard className="size-5" strokeWidth="1.5" />
					</div>
					<div class=" self-center truncate">{$i18n.t('Billing')}</div>
				</button>
			{/if}

			{#if ($config?.features?.enable_notes ?? false) && ($user?.role === 'admin' || ($user?.permissions?.features?.notes ?? true))}
				<div class="flex items-center w-full">
					<a
						href="/notes"
						draggable="false"
						class="flex flex-1 rounded-xl py-1.5 px-3 hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/notes');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center mr-3">
							<Note className="size-5" strokeWidth="1.5" />
						</div>
						<div class="self-center truncate">{$i18n.t('Notes')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('notes')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('notes')}
							>
								{#if isPinned('notes')}
									<PinSlash className="size-3.5" strokeWidth="1.5" />
								{:else}
									<Pin className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

			{#if $config?.features?.enable_calendar && ($user?.role === 'admin' || $user?.permissions?.features?.calendar)}
				<div class="flex items-center w-full">
					<a
						href="/calendar"
						draggable="false"
						class="flex flex-1 rounded-xl py-1.5 px-3 hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/calendar');
						}}
					>
						<div class="self-center mr-3">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5"
								/>
							</svg>
						</div>
						<div class="self-center truncate">{$i18n.t('Calendar')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('calendar')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('calendar')}
							>
								{#if isPinned('calendar')}
									<PinSlash className="size-3.5" strokeWidth="1.5" />
								{:else}
									<Pin className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

			{#if $config?.features?.enable_automations && ($user?.role === 'admin' || $user?.permissions?.features?.automations)}
				<div class="flex items-center w-full">
					<a
						href="/automations"
						draggable="false"
						class="flex flex-1 rounded-xl py-1.5 px-3 hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/automations');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center mr-3">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
								/>
							</svg>
						</div>
						<div class="self-center truncate">{$i18n.t('Automations')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('automations')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('automations')}
							>
								{#if isPinned('automations')}
									<PinSlash className="size-3.5" strokeWidth="1.5" />
								{:else}
									<Pin className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}

			{#if role === 'admin'}
				<div class="flex items-center w-full">
					<a
						href="/playground"
						draggable="false"
						class="flex flex-1 rounded-xl py-1.5 px-3 hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
						on:click={async (e) => {
							if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
							e.preventDefault();
							show = false;
							goto('/playground');
							if ($mobile) {
								await tick();
								showSidebar.set(false);
							}
						}}
					>
						<div class="self-center mr-3">
							<Code className="size-5" strokeWidth="1.5" />
						</div>
						<div class="self-center truncate">{$i18n.t('Playground')}</div>
					</a>
					{#if shiftKey}
						<Tooltip
							content={isPinned('playground')
								? $i18n.t('Unpin from Sidebar')
								: $i18n.t('Pin to Sidebar')}
						>
							<button
								type="button"
								class="p-1 mr-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition"
								on:click|preventDefault|stopPropagation={() => togglePin('playground')}
							>
								{#if isPinned('playground')}
									<PinSlash className="size-3.5" strokeWidth="1.5" />
								{:else}
									<Pin className="size-3.5" strokeWidth="1.5" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				</div>
				<a
					href="/admin/pii-dashboard"
					draggable="false"
					class="flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
					on:click={async (e) => {
						if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) {
							return;
						}
						e.preventDefault();
						show = false;
						goto('/admin/pii-dashboard');
						if ($mobile) {
							await tick();
							showSidebar.set(false);
						}
					}}
				>
					<div class=" self-center mr-3">
						<UserGroup className="w-5 h-5" strokeWidth="1.5" />
					</div>
					<div class=" self-center truncate">{$i18n.t('PII Dashboard')}</div>
				</a>
			{/if}

			{#if help}
				<hr class=" border-gray-50/30 dark:border-gray-800/30 my-1 p-0" />
=======
				class="flex h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] w-full hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
				type="button"
				on:click={async () => {
					show = false;
>>>>>>> v0.11.0

					await showSettings.set(true);

					if ($mobile) {
						await tick();
						showSidebar.set(false);
					}
				}}
			>
				<div class="self-center">
					<Settings className="size-3.5" strokeWidth="1.5" />
				</div>
				<div class=" self-center truncate">{$i18n.t('Settings')}</div>
			</button>

			<button
				class="flex h-[1.6875rem] items-center gap-2 rounded-xl px-2 text-[13px] w-full hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition cursor-pointer select-none"
				type="button"
				on:click={async () => {
					const res = await userSignOut();
					user.set(null);
					localStorage.removeItem('token');

					location.href = res?.redirect_url ?? '/';
					show = false;
				}}
			>
				<div class="self-center">
					<LogOutIcon className="size-3.5" strokeWidth="1.5" />
				</div>
				<div class=" self-center truncate">{$i18n.t('Sign Out')}</div>
			</button>
<<<<<<< HEAD

			{#if showActiveUsers && ($config?.features?.enable_public_active_users_count || role === 'admin') && usage}
				{#if usage?.user_count}
					<hr class=" border-gray-50/30 dark:border-gray-800/30 my-1 p-0" />

					<Tooltip
						content={usage?.model_ids && usage?.model_ids.length > 0
							? `${$i18n.t('Running')}: ${usage.model_ids.join(', ')} ✨`
							: ''}
					>
						<div
							class="flex rounded-xl py-1 px-3 text-xs gap-2.5 items-center"
							on:mouseenter={() => {
								if ($config?.features?.enable_public_active_users_count || role === 'admin') {
									getUsageInfo();
								}
							}}
						>
							<div class=" flex items-center">
								<span class="relative flex size-2">
									<span
										class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"
									></span>
									<span class="relative inline-flex rounded-full size-2 bg-green-500"></span>
								</span>
							</div>

							<div class=" ">
								<span class="">
									{$i18n.t('Active Users')}:
								</span>
								<span class=" font-semibold">
									{usage?.user_count}
								</span>
							</div>
						</div>
					</Tooltip>
				{/if}
			{/if}
		</div>
=======
		</DropdownMenu>
>>>>>>> v0.11.0
	</div>
</Dropdown>
