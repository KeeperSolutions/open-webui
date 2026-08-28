<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';
	import Sortable from 'sortablejs';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		user,
		chats,
		settings,
		chatId,
		tags,
		folders as _folders,
		showSidebar,
		showSearch,
		mobile,
		pinnedChats,
		pinnedNotes,
<<<<<<< HEAD
		scrollPaginationEnabled,
		currentChatPage,
=======
>>>>>>> v0.11.0
		temporaryChatEnabled,
		channels,
		socket,
		config,
		isApp,
		models,
		selectedFolder,
		WEBUI_NAME,
<<<<<<< HEAD
		billingStatus,
		sidebarWidth,
		activeChatIds
	} from '$lib/stores';
	import { onMount, getContext, tick } from 'svelte';
=======
		sidebarWidth
	} from '$lib/stores';
	import {
		loadNextChatListPage,
		refreshChatList,
		registerFolderRefreshHandler,
		setAllChatsRead,
		setChatActive,
		setChatReadAt
	} from '$lib/stores/chatList';
	import { onMount, getContext, tick, onDestroy } from 'svelte';
>>>>>>> v0.11.0

	const i18n = getContext('i18n');

	$: canImportChats = $user?.role === 'admin' || ($user?.permissions?.chat?.import ?? true);

	import {
		getAllTags,
		toggleChatPinnedStatusById,
		getChatById,
		updateChatFolderIdById,
<<<<<<< HEAD
		importChat
	} from '$lib/apis/chats';
	import { createNewFolder, getFolders, updateFolderParentIdById } from '$lib/apis/folders';
	import { isInternalUser } from '$lib/billing/planTiers';
	import { updateUserSettings } from '$lib/apis/users';
	import { checkActiveChats } from '$lib/apis/tasks';
	import { getPinnedNoteList, toggleNotePinnedStatusById } from '$lib/apis/notes';
=======
		importChats,
		deleteAllChats,
		getChatListBySearchText,
		markChatsRead
	} from '$lib/apis/chats';
	import {
		createNewFolder,
		getFolders,
		getSharedFolders,
		updateFolderParentIdById
	} from '$lib/apis/folders';
	import { createNewNote, getPinnedNoteList, toggleNotePinnedStatusById } from '$lib/apis/notes';
	import { updateUserSettings } from '$lib/apis/users';
>>>>>>> v0.11.0
	import { createNoteHandler } from '$lib/components/notes/utils';
	import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';

	import UserMenu from './Sidebar/UserMenu.svelte';
	import ChatItem from './Sidebar/ChatItem.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Loader from '../common/Loader.svelte';
	import Folder from '../common/Folder.svelte';
	import SidebarSection from './Sidebar/Section.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Folders from './Sidebar/Folders.svelte';
	import SharedFolderItem from './Sidebar/SharedFolderItem.svelte';
	import { getChannels, createNewChannel } from '$lib/apis/channels';
	import { getMyUsage, type MyUsage } from '$lib/apis/billing';
	import ChannelModal from './Sidebar/ChannelModal.svelte';
	import ChannelItem from './Sidebar/ChannelItem.svelte';
	import SearchModal from './SearchModal.svelte';
	import FolderModal from './Sidebar/Folders/FolderModal.svelte';
	import PinnedModelList from './Sidebar/PinnedModelList.svelte';
<<<<<<< HEAD
	import Note from '../icons/Note.svelte';
	import Code from '../icons/Code.svelte';
	import { slide } from 'svelte/transition';
=======
	import PinnedNoteList from './Sidebar/PinnedNoteList.svelte';
	import CalendarIcon from './Sidebar/icons/Calendar.svelte';
	import ClockIcon from './Sidebar/icons/Clock.svelte';
	import CodeIcon from './Sidebar/icons/Code.svelte';
	import EditPencilIcon from './Sidebar/icons/EditPencil.svelte';
	import NotesIcon from './Sidebar/icons/Notes.svelte';
	import SearchIcon from './Sidebar/icons/Search.svelte';
	import Sidebar from '../icons/Sidebar.svelte';
	import WorkspaceIcon from './Sidebar/icons/Workspace.svelte';
	import { slide } from 'svelte/transition';
	import HotkeyHint from '../common/HotkeyHint.svelte';
	import Dropdown from '../common/Dropdown.svelte';
	import DropdownMenu from '../common/DropdownMenu.svelte';
	import CheckIcon from '../icons/Check.svelte';
	import MoreHorizontalIcon from './Sidebar/icons/MoreHorizontal.svelte';
>>>>>>> v0.11.0

	const BREAKPOINT = 768;
	const DEFAULT_PINNED_ITEMS = ['notes', 'workspace'];

	let scrollTop = 0;

	let navElement;
	let shiftKey = false;

	let selectedChatId = null;
<<<<<<< HEAD
	let showPinnedChat = true;
=======

	// Keep the optimistic sidebar highlight in sync with the active chat. Leaving the
	// chat view (e.g. navigating to an admin page) clears chatId, and programmatic
	// navigation such as cloning moves chatId to a different chat — in both cases the
	// previously-selected item must not stay highlighted. The optimistic on-click
	// highlight is preserved because a click sets selectedChatId without changing
	// chatId, so this reactive only re-runs once chatId catches up to the same value.
	$: selectedChatId = $chatId || null;
>>>>>>> v0.11.0

	let showCreateChannel = false;

	let myUsage: MyUsage | null = null;
	let myUsageLoading = true;

	const loadMyUsage = async ({ retryIfEmpty = false } = {}) => {
		myUsageLoading = true;
		try {
			myUsage = await getMyUsage(localStorage.token);
			// ledger_ready is false when the poller hasn't completed its first sync yet.
			// Retry after 30s so the sidebar populates without waiting the full 5-minute poll interval.
			if (retryIfEmpty && myUsage && !myUsage.ledger_ready) {
				if (usageRetryTimer) clearTimeout(usageRetryTimer);
				usageRetryTimer = setTimeout(() => loadMyUsage({ retryIfEmpty: true }), 30 * 1000);
			}
		} catch {
			myUsage = null;
		} finally {
			myUsageLoading = false;
		}
	};

	let usagePollingInterval: ReturnType<typeof setInterval> | null = null;
	let usageRetryTimer: ReturnType<typeof setTimeout> | null = null;

	$: myUsageTooltip = myUsage
		? (() => {
				const monthName = new Date(myUsage.year, myUsage.month - 1).toLocaleString('default', {
					month: 'long'
				});
				return `<div class="text-left space-y-0.5"><div class="font-semibold mb-1">${monthName} ${myUsage.year}</div><div>Total tokens: ${myUsage.total_tokens.toLocaleString()}</div></div>`;
			})()
		: '';

	// Pagination variables
	let chatListLoading = false;
	let chatListReady = false;
	let allChatsLoaded = false;

	let showCreateFolderModal = false;

	let pinnedModels = [];

	let showPinnedModels = false;
	let showPinnedNotes = false;
	let showChannels = false;
	let showFolders = false;
	let showSharedFolders = false;
	let showChatsMenu = false;

	let folders = {};
	let folderRegistry: Record<
		string,
		{
			setFolderItems?: () => unknown;
			upsertChat?: (chat: Record<string, unknown>) => unknown;
			setChatActive?: (chatId: string, active: boolean) => boolean;
			setChatReadAt?: (chatId: string, lastReadAt: number) => boolean;
			setAllChatsRead?: () => unknown;
		}
	> = {};

	let newFolderId = null;

<<<<<<< HEAD
=======
	let sharedFolders: any[] = [];

>>>>>>> v0.11.0
	$: pinnedItems = $settings?.pinnedMenuItems ?? DEFAULT_PINNED_ITEMS;

	const isMenuItemVisible = (id) => {
		switch (id) {
			case 'notes':
				return (
					($config?.features?.enable_notes ?? false) &&
					($user?.role === 'admin' || ($user?.permissions?.features?.notes ?? true))
				);
			case 'workspace':
				return (
					$user?.role === 'admin' ||
					$user?.permissions?.workspace?.models ||
					$user?.permissions?.workspace?.knowledge ||
					$user?.permissions?.workspace?.prompts ||
					$user?.permissions?.workspace?.tools ||
					$user?.permissions?.workspace?.skills
				);
			case 'automations':
				return (
					$config?.features?.enable_automations &&
					($user?.role === 'admin' || $user?.permissions?.features?.automations)
				);
			case 'calendar':
				return (
					$config?.features?.enable_calendar &&
					($user?.role === 'admin' || $user?.permissions?.features?.calendar)
				);
			case 'playground':
				return $user?.role === 'admin';
			default:
				return false;
		}
	};

	const getMenuItemMeta = (id) => {
		const items = {
			notes: { label: 'Notes', href: '/notes', iconType: 'note' },
			workspace: { label: 'Workspace', href: '/workspace', iconType: 'workspace' },
			automations: { label: 'Automations', href: '/automations', iconType: 'automations' },
			calendar: { label: 'Calendar', href: '/calendar', iconType: 'calendar' },
			playground: { label: 'Playground', href: '/playground', iconType: 'playground' }
		};
		return items[id];
	};

<<<<<<< HEAD
=======
	const menuItemPathPrefixes = {
		notes: '/notes',
		workspace: '/workspace',
		calendar: '/calendar',
		automations: '/automations',
		playground: '/playground'
	};

	const getActiveMenuItemId = (pathname) => {
		for (const [id, pathPrefix] of Object.entries(menuItemPathPrefixes)) {
			if (pathname === pathPrefix || pathname.startsWith(`${pathPrefix}/`)) {
				return id;
			}
		}

		return null;
	};

	$: activeMenuItemId = getActiveMenuItemId($page.url.pathname);

>>>>>>> v0.11.0
	const initPinnedMenuSortable = () => {
		const el = document.getElementById('pinned-menu-items-list');
		if (el && !$mobile) {
			new Sortable(el, {
				animation: 150,
				onUpdate: async (event) => {
					const itemId = event.item.dataset.id;
					const newIndex = event.newIndex;
					const current = [...pinnedItems];
					const oldIndex = current.indexOf(itemId);
					current.splice(oldIndex, 1);
					current.splice(newIndex, 0, itemId);
					settings.set({ ...$settings, pinnedMenuItems: current });
					await updateUserSettings(localStorage.token, { ui: $settings });
				}
			});
		}
	};

	$: if ($selectedFolder) {
		initFolders();
	}

	const initFolders = async () => {
		const folderList = await getFolders(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return [];
		});
		_folders.set(folderList.sort((a, b) => b.updated_at - a.updated_at));

		folders = {};

		// First pass: Initialize all folder entries
		for (const folder of folderList) {
			// Ensure folder is added to folders with its data
			folders[folder.id] = { ...(folders[folder.id] || {}), ...folder };

			if (newFolderId && folder.id === newFolderId) {
				folders[folder.id].new = true;
				newFolderId = null;
			}
		}

		// Second pass: Tie child folders to their parents
		for (const folder of folderList) {
			if (folder.parent_id) {
				// Ensure the parent folder is initialized if it doesn't exist
				if (!folders[folder.parent_id]) {
					folders[folder.parent_id] = {}; // Create a placeholder if not already present
				}

				// Initialize childrenIds array if it doesn't exist and add the current folder id
				folders[folder.parent_id].childrenIds = folders[folder.parent_id].childrenIds
					? [...folders[folder.parent_id].childrenIds, folder.id]
					: [folder.id];

				// Sort the children by updated_at field
				folders[folder.parent_id].childrenIds.sort((a, b) => {
					return folders[b].updated_at - folders[a].updated_at;
				});
			}
		}

		// Merge shared folders into the same structure
		try {
			sharedFolders = await getSharedFolders(localStorage.token);
		} catch (e) {
			sharedFolders = [];
		}

		for (const sf of sharedFolders) {
			if (folders[sf.id]) continue; // Already owned by user
			folders[sf.id] = { ...sf, shared: true };
		}

		// Build parent-child relationships for shared folders
		for (const sf of sharedFolders) {
			if (folders[sf.id]?.shared && sf.parent_id && folders[sf.parent_id]) {
				folders[sf.parent_id].childrenIds = folders[sf.parent_id].childrenIds
					? [...new Set([...folders[sf.parent_id].childrenIds, sf.id])]
					: [sf.id];
			}
		}
	};

	const initSharedFolders = async () => {
		await initFolders();
	};

	const createFolder = async ({ name, data, parent_id }) => {
		name = name?.trim();
		if (!name) {
			toast.error($i18n.t('Folder name cannot be empty.'));
			return;
		}

		// Check for duplicate names in the same parent
		const siblings = Object.values(folders).filter((folder) => folder.parent_id === parent_id);
		if (siblings.find((folder) => folder.name.toLowerCase() === name.toLowerCase())) {
			// If a folder with the same name already exists, append a number to the name
			let i = 1;
			while (
				siblings.find((folder) => folder.name.toLowerCase() === `${name} ${i}`.toLowerCase())
			) {
				i++;
			}

			name = `${name} ${i}`;
		}

		// Add a dummy folder to the list to show the user that the folder is being created
		const tempId = uuidv4();
		folders = {
			...folders,
			[tempId]: {
				id: tempId,
				name: name,
				parent_id: parent_id,
				created_at: Date.now(),
				updated_at: Date.now()
			}
		};

		const res = await createNewFolder(localStorage.token, {
			name,
			data,
			parent_id
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			// newFolderId = res.id;
			await initFolders();
		}
	};

	const initChannels = async () => {
		try {
			await channels.set(await getChannels(localStorage.token));
		} catch {
			// Channels feature may not be enabled, continue without them
			await channels.set([]);
		}
	};

	const initChatList = async () => {
		// Reset pagination variables
<<<<<<< HEAD
		currentChatPage.set(1);
		allChatsLoaded = false;
=======
		console.log('initChatList');
		allChatsLoaded = false;
		chatListReady = false;
>>>>>>> v0.11.0

		initFolders();
		initSharedFolders();
		await Promise.all([
<<<<<<< HEAD
			await (async () => {
				const _tags = await getAllTags(localStorage.token);
				tags.set(_tags);
			})(),
			await (async () => {
				const _pinnedChats = await getPinnedChatList(localStorage.token);
				pinnedChats.set(_pinnedChats);
			})(),
			await (async () => {
				if ($config?.features?.enable_notes && ($user?.role === 'admin' || ($user?.permissions?.features?.notes ?? true))) {
					const _pinnedNotes = await getPinnedNoteList(localStorage.token).catch(() => []);
					pinnedNotes.set(_pinnedNotes);
				}
			})(),
			await (async () => {
				const _chats = await getChatList(localStorage.token, $currentChatPage);
				await chats.set(_chats);
=======
			(async () => {
				console.log('Init tags');
				const _tags = await getAllTags(localStorage.token);
				tags.set(_tags);
			})(),
			(async () => {
				if (
					$config?.features?.enable_notes &&
					($user?.role === 'admin' || ($user?.permissions?.features?.notes ?? true))
				) {
					console.log('Init pinned notes');
					const _pinnedNotes = await getPinnedNoteList(localStorage.token).catch(() => []);
					pinnedNotes.set(_pinnedNotes);
				}
			})(),
			(async () => {
				console.log('Init chat list');
				await refreshChatRows();
>>>>>>> v0.11.0
			})()
		]);
	};

	const refreshChatRows = async () => {
		const result = await refreshChatList(localStorage.token, { refreshPinned: true });
		if (result.accepted) {
			await initFolders();
			await Promise.all(Object.values(folderRegistry).map((folder) => folder?.setFolderItems?.()));
			allChatsLoaded = result.allLoaded;
			chatListReady = true;
		}
	};

	const loadMoreChats = async () => {
		chatListLoading = true;

		const result = await loadNextChatListPage(localStorage.token);
		allChatsLoaded = result.allLoaded;

		chatListLoading = false;
	};

	const applyFolderUnreadCounts = (folderUnreadCounts: Record<string, number>) => {
		folders = Object.fromEntries(
			Object.entries(folders).map(([id, folder]) => [
				id,
				id in folderUnreadCounts ? { ...folder, unread_count: folderUnreadCounts[id] } : folder
			])
		);
		_folders.update((folderList) =>
			folderList.map((folder) =>
				folder.id in folderUnreadCounts
					? { ...folder, unread_count: folderUnreadCounts[folder.id] }
					: folder
			)
		);
	};

	const applyChatReadState = (data) => {
		if (data?.folder_unread_counts) {
			applyFolderUnreadCounts(data.folder_unread_counts);
		}

		if (data?.chat_id && typeof data?.last_read_at === 'number') {
			setChatReadAt(data.chat_id, data.last_read_at);
			for (const folder of Object.values(folderRegistry)) {
				folder?.setChatReadAt?.(data.chat_id, data.last_read_at);
			}
		}
	};

	const markAllChatsReadHandler = async () => {
		const res = await markChatsRead(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (!res) return;

		showChatsMenu = false;
		if (res.folder_unread_counts) {
			applyFolderUnreadCounts(res.folder_unread_counts);
		}
		setAllChatsRead();
		for (const folder of Object.values(folderRegistry)) {
			folder?.setAllChatsRead?.();
		}
	};

	const importChatHandler = async (items, pinned = false, folderId = null) => {
<<<<<<< HEAD
=======
		if (!canImportChats) {
			toast.error($i18n.t('Access prohibited'));
			return;
		}

		console.log('importChatHandler', items, pinned, folderId);
>>>>>>> v0.11.0
		for (const item of items) {
			if (item.chat) {
				await importChat(
					localStorage.token,
					item.chat,
					item?.meta ?? {},
					pinned,
					folderId,
					item?.created_at ?? null,
					item?.updated_at ?? null
				);
			}
		}

		initChatList();
	};

	const inputFilesHandler = async (files) => {
		for (const file of files) {
			const reader = new FileReader();
			reader.onload = async (e) => {
				const content = e.target.result;

				try {
					const chatItems = JSON.parse(content);
					importChatHandler(chatItems);
				} catch {
					toast.error($i18n.t(`Invalid file format.`));
				}
			};

			reader.readAsText(file);
		}
	};

	const tagEventHandler = async (type, tagName, chatId) => {
		if (type === 'delete') {
			initChatList();
		} else if (type === 'add') {
			initChatList();
		}
	};

	let draggedOver = false;

	const onDragOver = (e) => {
		e.preventDefault();

		// Check if a file is being draggedOver.
		if (e.dataTransfer?.types?.includes('Files')) {
			draggedOver = true;
		} else {
			draggedOver = false;
		}
	};

	const onDragLeave = () => {
		draggedOver = false;
	};

	const onDrop = async (e) => {
		e.preventDefault();

		// Perform file drop check and handle it accordingly
		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);

			if (inputFiles && inputFiles.length > 0) {
				inputFilesHandler(inputFiles); // Handle the dropped files
			}
		}

		draggedOver = false; // Reset draggedOver status after drop
	};

	let touchstart;
	let touchend;

	function checkDirection() {
		const screenWidth = window.innerWidth;
		const swipeDistance = Math.abs(touchend.screenX - touchstart.screenX);
		if (touchstart.clientX < 40 && swipeDistance >= screenWidth / 8) {
			if (touchend.screenX < touchstart.screenX) {
				showSidebar.set(false);
			}
			if (touchend.screenX > touchstart.screenX) {
				showSidebar.set(true);
			}
		}
	}

	const onTouchStart = (e) => {
		touchstart = e.changedTouches[0];
	};

	const onTouchEnd = (e) => {
		touchend = e.changedTouches[0];
		checkDirection();
	};

	const onKeyDown = (e) => {
		if (e.key === 'Shift') {
			shiftKey = true;
		}
	};

	const onKeyUp = (e) => {
		if (e.key === 'Shift') {
			shiftKey = false;
		}
	};

	const onFocus = () => {};

	const onBlur = () => {
		shiftKey = false;
		selectedChatId = null;
	};

	const MIN_WIDTH = 220;
	const MAX_WIDTH = 480;

	let isResizing = false;

	let startWidth = 0;
	let startClientX = 0;

	const resizeStartHandler = (e: MouseEvent) => {
		if ($mobile) return;
		isResizing = true;

		startClientX = e.clientX;
		startWidth = $sidebarWidth ?? 245;

		document.body.style.userSelect = 'none';
	};

	const resizeEndHandler = () => {
		if (!isResizing) return;
		isResizing = false;

		document.body.style.userSelect = '';
		localStorage.setItem('sidebarWidth', String($sidebarWidth));
	};

	const resizeSidebarHandler = (endClientX) => {
		const dx = endClientX - startClientX;
		const newSidebarWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + dx));

		sidebarWidth.set(newSidebarWidth);
		document.documentElement.style.setProperty('--sidebar-width', `${newSidebarWidth}px`);
	};

<<<<<<< HEAD
	const RESIZE_KEY_STEP = 20;

	const resizeKeyHandler = (e: KeyboardEvent) => {
		if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
		e.preventDefault();

		const delta = e.key === 'ArrowRight' ? RESIZE_KEY_STEP : -RESIZE_KEY_STEP;
		const newSidebarWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, ($sidebarWidth ?? 260) + delta));

		sidebarWidth.set(newSidebarWidth);
		document.documentElement.style.setProperty('--sidebar-width', `${newSidebarWidth}px`);
		localStorage.setItem('sidebarWidth', String(newSidebarWidth));
	};

	onMount(async () => {
		showPinnedChat = localStorage?.showPinnedChat ? localStorage.showPinnedChat === 'true' : true;
		loadMyUsage({ retryIfEmpty: true });
		// Use configured poll interval from backend (default 2 minutes), converted to milliseconds
		const pollIntervalMs = ($billingStatus?.usage_poll_interval_seconds ?? 120) * 1000;
		usagePollingInterval = setInterval(loadMyUsage, pollIntervalMs);
		await showSidebar.set(!$mobile ? localStorage.sidebar === 'true' : false);

=======
	onMount(async () => {
>>>>>>> v0.11.0
		try {
			const width = Number(localStorage.getItem('sidebarWidth'));
			if (!Number.isNaN(width) && width >= MIN_WIDTH && width <= MAX_WIDTH) {
				sidebarWidth.set(width);
			}
		} catch {}

		document.documentElement.style.setProperty('--sidebar-width', `${$sidebarWidth}px`);
		sidebarWidth.subscribe((w) => {
			document.documentElement.style.setProperty('--sidebar-width', `${w}px`);
		});

		showSidebar.set(!$mobile ? localStorage.sidebar === 'true' : false);

		const unsubscribers = [
			mobile.subscribe((value) => {
				if ($showSidebar && value) {
					showSidebar.set(false);
				}

				if ($showSidebar && !value) {
					const navElement = document.getElementsByTagName('nav')[0];
					if (navElement) {
						navElement.style['-webkit-app-region'] = 'drag';
					}
				}

				if (!$showSidebar && !value) {
					showSidebar.set(true);
				}
			}),
			showSidebar.subscribe(async (value) => {
				localStorage.sidebar = value;

				// nav element is not available on the first render
				const navElement = document.getElementsByTagName('nav')[0];

				if (navElement) {
					if ($mobile) {
						if (!value) {
							navElement.style['-webkit-app-region'] = 'drag';
						} else {
							navElement.style['-webkit-app-region'] = 'no-drag';
						}
					} else {
						navElement.style['-webkit-app-region'] = 'drag';
					}
				}

				if (value) {
					await initChannels();
					await initChatList();
				}
			})
		];

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);

		window.addEventListener('touchstart', onTouchStart);
		window.addEventListener('touchend', onTouchEnd);

		window.addEventListener('focus', onFocus);
		window.addEventListener('blur', onBlur);

		const dropZone = document.getElementById('sidebar');
		if (dropZone) {
			dropZone.addEventListener('dragover', onDragOver);
			dropZone.addEventListener('drop', onDrop);
			dropZone.addEventListener('dragleave', onDragLeave);
		}

		const socketInstance = $socket;
		socketInstance?.on('events', chatActiveEventHandler);
		socketInstance?.on('connect', refreshChatRows);

		const unregisterFolderRefreshHandler = registerFolderRefreshHandler((folderId, chat) => {
			if (folderId) {
				if (chat) {
					return folderRegistry[folderId]?.upsertChat?.(chat);
				}

				return folderRegistry[folderId]?.setFolderItems?.();
			}

			return Promise.all(Object.values(folderRegistry).map((folder) => folder?.setFolderItems?.()));
		});

		await tick();
		initPinnedMenuSortable();

		await tick();
		initPinnedMenuSortable();

		return () => {
			unsubscribers.forEach((unsubscriber) => unsubscriber());

			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('keyup', onKeyUp);

			window.removeEventListener('touchstart', onTouchStart);
			window.removeEventListener('touchend', onTouchEnd);

			window.removeEventListener('focus', onFocus);
			window.removeEventListener('blur', onBlur);

			if (dropZone) {
				dropZone.removeEventListener('dragover', onDragOver);
				dropZone.removeEventListener('drop', onDrop);
				dropZone.removeEventListener('dragleave', onDragLeave);
			}

			if (usagePollingInterval) clearInterval(usagePollingInterval);
			if (usageRetryTimer) clearTimeout(usageRetryTimer);

			socketInstance?.off('events', chatActiveEventHandler);
			socketInstance?.off('connect', refreshChatRows);

			unregisterFolderRefreshHandler();
		};
	});

	// Handler for chat events (defined outside onMount for proper cleanup)
<<<<<<< HEAD
	const chatActiveEventHandler = (event: {
=======
	const chatActiveEventHandler = async (event: {
>>>>>>> v0.11.0
		chat_id: string;
		message_id: string;
		data: {
			type: string;
			data: {
				active?: boolean;
				folder_id?: string | null;
				last_read_at?: number;
				folder_unread_counts?: Record<string, number>;
			};
		};
	}) => {
		if (event.data?.type === 'chat:active') {
			const eventData = event.data.data ?? {};
			const active = eventData.active ?? false;
			const found = setChatActive(event.chat_id, active);
			let foundInFolder = false;
			for (const folder of Object.values(folderRegistry)) {
				foundInFolder = folder?.setChatActive?.(event.chat_id, active) || foundInFolder;
			}
			if (!foundInFolder && active && eventData.folder_id) {
				await folderRegistry[eventData.folder_id]?.setFolderItems?.();
			}
			if (!found && active) {
				await refreshChatRows();
			}
		} else if (event.data?.type === 'chat:list') {
			const eventData = event.data.data ?? {};
			const folderUnreadCounts = eventData.folder_unread_counts;
			if (folderUnreadCounts) {
				applyFolderUnreadCounts(folderUnreadCounts);
			}

			if (typeof eventData.last_read_at === 'number') {
				setChatReadAt(event.chat_id, eventData.last_read_at);
				for (const folder of Object.values(folderRegistry)) {
					folder?.setChatReadAt?.(event.chat_id, eventData.last_read_at);
				}
<<<<<<< HEAD
				return newSet;
			});
		} else if (event.data?.type === 'chat:list') {
			initChatList();
=======
				return;
			}

			await refreshChatRows();
			if (eventData.folder_id) {
				await folderRegistry[eventData.folder_id]?.setFolderItems?.();
			}
>>>>>>> v0.11.0
		}
	};

	const newChatHandler = async () => {
		selectedChatId = null;
		selectedFolder.set(null);

		if ($user?.role !== 'admin' && $user?.permissions?.chat?.temporary_enforced) {
			await temporaryChatEnabled.set(true);
		} else {
			await temporaryChatEnabled.set(false);
		}

		setTimeout(() => {
			document.getElementById('new-chat-button')?.click();

			if ($mobile) {
				showSidebar.set(false);
			}
		}, 0);
	};

	const itemClickHandler = async () => {
		selectedChatId = null;
		chatId.set('');

		if ($mobile) {
			showSidebar.set(false);
		}

		await tick();
	};

	const isWindows = /Windows/i.test(navigator.userAgent);
</script>

<ChannelModal
	bind:show={showCreateChannel}
	onSubmit={async (payload: any) => {
		let { type, name, is_private, access_grants, group_ids, user_ids } = payload ?? {};
		name = name?.trim();

		if (type === 'dm') {
			if (!user_ids || user_ids.length === 0) {
				toast.error($i18n.t('Please select at least one user for Direct Message channel.'));
				return;
			}
		} else {
			if (!name) {
				toast.error($i18n.t('Channel name cannot be empty.'));
				return;
			}
		}

		const res = await createNewChannel(localStorage.token, {
			name: name,
			is_private: is_private,
			access_grants: access_grants,
			group_ids: group_ids,
			user_ids: user_ids
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			$socket.emit('join-channels', { auth: { token: $user?.token } });
			await initChannels();
			showCreateChannel = false;
		}
	}}
/>

<FolderModal
	bind:show={showCreateFolderModal}
	onSubmit={async (folder) => {
		await createFolder(folder);
		showCreateFolderModal = false;
	}}
/>

<!-- svelte-ignore a11y-no-static-element-interactions -->

{#if $showSidebar}
	<div
		class=" {$isApp
			? ' ml-[4.5rem] md:ml-0'
			: ''} fixed md:hidden z-40 top-0 right-0 left-0 bottom-0 bg-black/60 w-full min-h-screen h-screen flex justify-center overflow-hidden overscroll-contain"
		on:mousedown={() => {
			showSidebar.set(!$showSidebar);
		}}
	></div>
{/if}

<SearchModal
	bind:show={$showSearch}
	onClose={() => {
		if ($mobile) {
			showSidebar.set(false);
		}
	}}
/>

<button
	id="sidebar-new-chat-button"
	class="hidden"
	aria-label="New Chat"
	on:click={() => {
		goto('/chat');
		newChatHandler();
	}}
></button>

<svelte:window
	on:mousemove={(e) => {
		if (!isResizing) return;
		resizeSidebarHandler(e.clientX);
	}}
	on:mouseup={() => {
		resizeEndHandler();
	}}
/>

{#if !$mobile && !$showSidebar}
	<div
<<<<<<< HEAD
		class=" py-2 px-1.5 flex flex-col justify-between text-black dark:text-white hover:bg-gray-50/50 dark:hover:bg-gray-950/50 h-full border-e border-gray-50 dark:border-gray-850 z-10 transition-all"
=======
		class=" w-[42px] shrink-0 py-1 px-1 flex flex-col justify-between text-gray-700 dark:text-gray-300 hover:bg-gray-50/30 dark:hover:bg-gray-800/30 h-full z-10 transition-all border-e-[0.5px] border-gray-50 dark:border-gray-850/30"
>>>>>>> v0.11.0
		id="sidebar"
		role="navigation"
		aria-label={$i18n.t('Chat history')}
	>
		<button
			class="flex flex-col flex-1 {isWindows ? 'cursor-pointer' : 'cursor-[e-resize]'}"
			on:click={async () => {
				showSidebar.set(!$showSidebar);
			}}
		>
			<div class="pb-1">
				<Tooltip
					content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
					placement="right"
				>
					<button
						class="flex size-8.5 items-center justify-center transition group {isWindows
							? 'cursor-pointer'
							: 'cursor-[e-resize]'}"
						aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
					>
						<div
							class=" self-center flex size-[30px] items-center justify-center rounded-lg transition group-hover:bg-gray-50 dark:group-hover:bg-gray-900"
						>
							<img
<<<<<<< HEAD
								crossorigin="anonymous"
								src="/hubgate/hubgate-logomark.svg"
								class="sidebar-new-chat-icon size-6 group-hover:hidden"
								alt="Hubgate"
=======
								src="{WEBUI_BASE_URL}/static/favicon.png"
								class="sidebar-new-chat-icon size-5 rounded-full group-hover:hidden"
								alt=""
>>>>>>> v0.11.0
							/>

							<Sidebar className="size-4 hidden group-hover:flex" />
						</div>
					</button>
				</Tooltip>
			</div>

<<<<<<< HEAD
			<div>
				<div class="">
					<Tooltip content={$i18n.t('New Chat')} placement="right">
						<a
							class=" cursor-pointer flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition group"
							href="/chat"
=======
			<div class="-gap-0.5">
				<div class="">
					<Tooltip content={$i18n.t('New Chat')} placement="right">
						<a
							class=" cursor-pointer flex size-8 items-center justify-center transition group"
							href="/"
>>>>>>> v0.11.0
							draggable="false"
							on:click={async (e) => {
								e.stopImmediatePropagation();
								e.preventDefault();

								goto('/chat');
								newChatHandler();
							}}
							aria-label={$i18n.t('New Chat')}
						>
							<div
								class=" self-center flex size-[30px] items-center justify-center rounded-lg transition group-hover:bg-gray-50 dark:group-hover:bg-gray-900"
							>
								<EditPencilIcon className="size-4" strokeWidth="1.5" />
							</div>
						</a>
					</Tooltip>
				</div>

				<div class="">
					<Tooltip content={$i18n.t('Search')} placement="right">
						<button
							class=" cursor-pointer flex size-8 items-center justify-center transition group"
							on:click={(e) => {
								e.stopImmediatePropagation();
								e.preventDefault();

								showSearch.set(true);
							}}
							draggable="false"
							aria-label={$i18n.t('Search')}
						>
							<div
								class=" self-center flex size-[30px] items-center justify-center rounded-lg transition group-hover:bg-gray-50 dark:group-hover:bg-gray-900"
							>
								<SearchIcon className="size-4" strokeWidth="1.5" />
							</div>
						</button>
					</Tooltip>
				</div>

				{#each pinnedItems as itemId (itemId)}
					{@const meta = getMenuItemMeta(itemId)}
					{#if meta && isMenuItemVisible(itemId)}
						<div class="">
							<Tooltip content={$i18n.t(meta.label)} placement="right">
								<a
<<<<<<< HEAD
									class=" cursor-pointer flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition group"
=======
									class=" cursor-pointer flex size-8 items-center justify-center transition group"
>>>>>>> v0.11.0
									href={meta.href}
									on:click={async (e) => {
										e.stopImmediatePropagation();
										e.preventDefault();
										goto(meta.href);
										itemClickHandler();
									}}
									draggable="false"
									aria-label={$i18n.t(meta.label)}
								>
<<<<<<< HEAD
									<div class=" self-center flex items-center justify-center size-9">
										{#if itemId === 'notes'}
											<Note className="size-4.5" />
										{:else if itemId === 'workspace'}
											<svg
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
												stroke-width="1.5"
												stroke="currentColor"
												class="size-4.5"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 0 0 2.25-2.25V6a2.25 2.25 0 0 0-2.25-2.25H6A2.25 2.25 0 0 0 3.75 6v2.25A2.25 2.25 0 0 0 6 10.5Zm0 9.75h2.25A2.25 2.25 0 0 0 10.5 18v-2.25a2.25 2.25 0 0 0-2.25-2.25H6a2.25 2.25 0 0 0-2.25 2.25V18A2.25 2.25 0 0 0 6 20.25Zm9.75-9.75H18a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 18 3.75h-2.25A2.25 2.25 0 0 0 13.5 6v2.25a2.25 2.25 0 0 0 2.25 2.25Z"
												/>
											</svg>
										{:else if itemId === 'automations'}
											<svg
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
												stroke-width="1.5"
												stroke="currentColor"
												class="size-4.5"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
												/>
											</svg>
										{:else if itemId === 'calendar'}
											<svg
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
												stroke-width="1.5"
												stroke="currentColor"
												class="size-4.5"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5"
												/>
											</svg>
										{:else if itemId === 'playground'}
											<Code className="size-4.5" />
=======
									<div
										class=" self-center flex size-[30px] items-center justify-center rounded-lg transition {itemId ===
										activeMenuItemId
											? ($settings?.highContrastMode ?? false)
												? 'bg-black/[0.035] dark:bg-white/[0.06]'
												: 'bg-black/[0.035] dark:bg-white/[0.045]'
											: 'group-hover:bg-gray-50 dark:group-hover:bg-gray-900'}"
									>
										{#if itemId === 'notes'}
											<NotesIcon className="size-4" strokeWidth="1.5" />
										{:else if itemId === 'workspace'}
											<WorkspaceIcon className="size-4" strokeWidth="1.5" />
										{:else if itemId === 'automations'}
											<ClockIcon className="size-4" strokeWidth="1.5" />
										{:else if itemId === 'calendar'}
											<CalendarIcon className="size-4" strokeWidth="1.5" />
										{:else if itemId === 'playground'}
											<CodeIcon className="size-4" strokeWidth="1.5" />
>>>>>>> v0.11.0
										{/if}
									</div>
								</a>
							</Tooltip>
						</div>
					{/if}
				{/each}
			</div>
		</button>

		<div>
			<div>
<<<<<<< HEAD
				<div class=" py-0.5">
					{#if $user !== undefined && $user !== null}
						<UserMenu
							role={$user?.role}
							profile={$config?.features?.enable_user_status ?? true}
							showActiveUsers={false}
							on:show={(e) => {
								if (e.detail === 'archived-chat') {
									showArchivedChats.set(true);
								}
							}}
						>
							<button
								type="button"
								class=" cursor-pointer flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition group"
								aria-label={$i18n.t('User menu')}
							>
								<div class="self-center flex items-center justify-center size-9 relative">
									<img
										src={$user?.profile_image_url}
										class=" size-6 object-cover rounded-full"
=======
				<div class=" flex justify-center items-center">
					{#if $user !== undefined && $user !== null}
						<UserMenu role={$user?.role} profile={$config?.features?.enable_user_status ?? true}>
							<button
								type="button"
								class=" cursor-pointer flex size-8.5 items-center justify-center transition group"
								aria-label={$i18n.t('User menu')}
							>
								<div
									class="self-center relative flex size-[30px] items-center justify-center rounded-lg transition group-hover:bg-gray-50 dark:group-hover:bg-gray-900"
								>
									<img
										src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
										class="size-5.5 object-cover rounded-full"
>>>>>>> v0.11.0
										alt={$i18n.t('Open User Profile Menu')}
										aria-label={$i18n.t('Open User Profile Menu')}
									/>

									{#if $config?.features?.enable_user_status}
										<div class="absolute -bottom-0.5 -right-0.5">
											<span class="relative flex size-2.5">
												<span
													class="relative inline-flex size-2.5 rounded-full {true
														? 'bg-green-500'
														: 'bg-gray-300 dark:bg-gray-700'} border-2 border-white dark:border-gray-900"
												></span>
											</span>
										</div>
									{/if}
								</div>
							</button>
						</UserMenu>
					{/if}
				</div>
			</div>
		</div>
	</div>
{/if}

{#if $showSidebar}
	<div
		bind:this={navElement}
		id="sidebar"
		role="navigation"
		aria-label={$i18n.t('Chat history')}
		class="h-screen max-h-[100dvh] min-h-screen select-none {$showSidebar
			? 'bg-gray-50 dark:bg-gray-950 z-50'
			: ' bg-transparent z-0 '} {$isApp
			? `ml-[4.5rem] md:ml-0 `
			: ' transition-all duration-300 '} shrink-0 text-gray-700 dark:text-gray-300 text-[13px] leading-5 fixed top-0 left-0 overflow-x-hidden
        "
		transition:slide={{ duration: 250, axis: 'x' }}
		data-state={$showSidebar}
	>
		<div
			class=" my-auto flex flex-col justify-between h-screen max-h-[100dvh] w-[var(--sidebar-width)] overflow-x-hidden scrollbar-hidden z-50 border-e border-gray-50 dark:border-gray-850/30 {$showSidebar
				? ''
				: 'invisible'}"
		>
			<div
<<<<<<< HEAD
				class="sidebar px-2 pt-2 pb-1.5 flex justify-between space-x-1 text-gray-600 dark:text-gray-400 sticky top-0 z-10 -mb-3"
			>
				<a
					class="flex items-center rounded-xl size-8.5 h-full justify-center hover:bg-gray-100/50 dark:hover:bg-gray-850/50 transition no-drag-region"
					href="/chat"
=======
				class="sidebar px-1 pt-1.5 pb-1 flex justify-between space-x-1 text-gray-600 dark:text-gray-400 sticky top-0 z-10 -mb-2"
			>
				<a
					class="flex items-center rounded-xl size-8.5 h-full justify-center hover:bg-gray-50 dark:hover:bg-gray-900 transition no-drag-region"
					href="/"
>>>>>>> v0.11.0
					draggable="false"
					on:click={newChatHandler}
				>
					<img
						crossorigin="anonymous"
<<<<<<< HEAD
						src="/hubgate/hubgate-logomark.svg"
						class="sidebar-new-chat-icon size-6"
						alt="Hubgate"
					/>
				</a>

				<a href="/chat" class="flex flex-1 px-0.5" on:click={newChatHandler}>
=======
						src="{WEBUI_BASE_URL}/static/favicon.png"
						class="sidebar-new-chat-icon size-5 rounded-full"
						alt=""
					/>
				</a>

				<a href="/" class="flex flex-1 px-0.5" on:click={newChatHandler}>
>>>>>>> v0.11.0
					<div
						id="sidebar-webui-name"
						class=" self-center font-normal text-gray-700 dark:text-gray-200"
					>
						{$WEBUI_NAME}
					</div>
				</a>
				<Tooltip
					content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
					placement="bottom"
				>
					<button
						class="flex size-[30px] justify-center items-center rounded-lg hover:bg-gray-50 dark:hover:bg-gray-900 transition {isWindows
							? 'cursor-pointer'
							: 'cursor-[w-resize]'}"
						on:click={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
					>
						<div class=" self-center">
							<Sidebar className="size-4" />
						</div>
					</button>
				</Tooltip>

				<div
					class="{scrollTop > 0
						? 'visible'
						: 'invisible'} sidebar-bg-gradient-to-b bg-linear-to-b from-gray-50 dark:from-gray-950 to-transparent from-50% pointer-events-none absolute inset-0 -z-10 -mb-6"
				></div>
			</div>

			<div
				class="relative flex flex-col flex-1 overflow-y-auto scrollbar-hidden pt-2.5 pb-2.5"
				on:scroll={(e) => {
					if (e.target.scrollTop === 0) {
						scrollTop = 0;
					} else {
						scrollTop = e.target.scrollTop;
					}
				}}
			>
<<<<<<< HEAD
				<div class="pb-1.5">
					<div class="px-[7px] flex justify-center text-gray-800 dark:text-gray-200">
						<a
							id="sidebar-new-chat-button"
							class="grow flex items-center space-x-3 rounded-2xl px-2.5 py-2 hover:bg-gray-100 dark:hover:bg-gray-900 transition outline-none"
							href="/chat"
=======
				<div class="pb-1">
					<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
						<a
							id="sidebar-new-chat-button"
							class="group grow flex items-center space-x-2 rounded-xl px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-900 transition outline-none"
							href="/"
>>>>>>> v0.11.0
							draggable="false"
							on:click={newChatHandler}
							aria-label={$i18n.t('New Chat')}
						>
							<div class="self-center flex size-4 shrink-0 items-center justify-center">
								<EditPencilIcon className=" size-4" strokeWidth="1.5" />
							</div>

<<<<<<< HEAD
							<div class="flex self-center translate-y-[0.5px]">
								<div class=" self-center text-sm font-primary">{$i18n.t('New Chat')}</div>
=======
							<div class="flex flex-1 self-center translate-y-[0.5px]">
								<div class=" self-center text-[13px] leading-5">{$i18n.t('New Chat')}</div>
>>>>>>> v0.11.0
							</div>
						</a>
					</div>

<<<<<<< HEAD
					<div class="px-[7px] flex justify-center text-gray-800 dark:text-gray-200">
						<button
							id="sidebar-search-button"
							class="grow flex items-center space-x-3 rounded-2xl px-2.5 py-2 hover:bg-gray-100 dark:hover:bg-gray-900 transition outline-none"
=======
					<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
						<button
							id="sidebar-search-button"
							class="group grow flex items-center space-x-2 rounded-xl px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-900 transition outline-none"
>>>>>>> v0.11.0
							on:click={() => {
								showSearch.set(true);
							}}
							draggable="false"
							aria-label={$i18n.t('Search')}
						>
							<div class="self-center flex size-4 shrink-0 items-center justify-center">
								<SearchIcon strokeWidth="1.5" className="size-4" />
							</div>

<<<<<<< HEAD
							<div class="flex self-center translate-y-[0.5px]">
								<div class=" self-center text-sm font-primary">{$i18n.t('Search')}</div>
=======
							<div class="flex flex-1 self-center translate-y-[0.5px]">
								<div class=" self-center text-[13px] leading-5">{$i18n.t('Search')}</div>
>>>>>>> v0.11.0
							</div>
						</button>
					</div>

					<div id="pinned-menu-items-list">
						{#each pinnedItems as itemId (itemId)}
							{@const meta = getMenuItemMeta(itemId)}
							{#if meta && isMenuItemVisible(itemId)}
								<div
<<<<<<< HEAD
									class="px-[0.4375rem] flex justify-center text-gray-800 dark:text-gray-200"
=======
									class="px-1 flex justify-center text-gray-700 dark:text-gray-300"
>>>>>>> v0.11.0
									data-id={itemId}
								>
									<a
										id="sidebar-{itemId}-button"
<<<<<<< HEAD
										class="grow flex items-center space-x-3 rounded-2xl px-2.5 py-2 hover:bg-gray-100 dark:hover:bg-gray-900 transition"
=======
										class="grow flex items-center space-x-2 rounded-xl px-2 py-1.5 transition {itemId ===
										activeMenuItemId
											? ($settings?.highContrastMode ?? false)
												? 'bg-black/[0.035] dark:bg-white/[0.06]'
												: 'bg-black/[0.035] dark:bg-white/[0.045]'
											: 'hover:bg-gray-50 dark:hover:bg-gray-900'}"
>>>>>>> v0.11.0
										href={meta.href}
										on:click={itemClickHandler}
										draggable="false"
										aria-label={$i18n.t(meta.label)}
									>
<<<<<<< HEAD
										<div class="self-center">
											{#if itemId === 'notes'}
												<Note className="size-4.5" strokeWidth="2" />
											{:else if itemId === 'workspace'}
												<svg
													xmlns="http://www.w3.org/2000/svg"
													fill="none"
													viewBox="0 0 24 24"
													stroke-width="2"
													stroke="currentColor"
													class="size-4.5"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														d="M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 0 0 2.25-2.25V6a2.25 2.25 0 0 0-2.25-2.25H6A2.25 2.25 0 0 0 3.75 6v2.25A2.25 2.25 0 0 0 6 10.5Zm0 9.75h2.25A2.25 2.25 0 0 0 10.5 18v-2.25a2.25 2.25 0 0 0-2.25-2.25H6a2.25 2.25 0 0 0-2.25 2.25V18A2.25 2.25 0 0 0 6 20.25Zm9.75-9.75H18a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 18 3.75h-2.25A2.25 2.25 0 0 0 13.5 6v2.25a2.25 2.25 0 0 0 2.25 2.25Z"
													/>
												</svg>
											{:else if itemId === 'automations'}
												<svg
													xmlns="http://www.w3.org/2000/svg"
													fill="none"
													viewBox="0 0 24 24"
													stroke-width="2"
													stroke="currentColor"
													class="size-4.5"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
													/>
												</svg>
											{:else if itemId === 'calendar'}
												<svg
													xmlns="http://www.w3.org/2000/svg"
													fill="none"
													viewBox="0 0 24 24"
													stroke-width="2"
													stroke="currentColor"
													class="size-4.5"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5"
													/>
												</svg>
											{:else if itemId === 'playground'}
												<Code className="size-4.5" strokeWidth="2" />
=======
										<div class="self-center flex size-4 shrink-0 items-center justify-center">
											{#if itemId === 'notes'}
												<NotesIcon className="size-4" strokeWidth="1.5" />
											{:else if itemId === 'workspace'}
												<WorkspaceIcon className="size-4" strokeWidth="1.5" />
											{:else if itemId === 'automations'}
												<ClockIcon className="size-4" strokeWidth="1.5" />
											{:else if itemId === 'calendar'}
												<CalendarIcon className="size-4" strokeWidth="1.5" />
											{:else if itemId === 'playground'}
												<CodeIcon className="size-4" strokeWidth="1.5" />
>>>>>>> v0.11.0
											{/if}
										</div>

										<div class="flex self-center translate-y-[0.5px]">
<<<<<<< HEAD
											<div class=" self-center text-sm font-primary">{$i18n.t(meta.label)}</div>
=======
											<div class=" self-center text-[13px] leading-5">{$i18n.t(meta.label)}</div>
>>>>>>> v0.11.0
										</div>
									</a>
								</div>
							{/if}
						{/each}
					</div>
				</div>

<<<<<<< HEAD
				{#if ($models ?? []).length > 0 && ($settings?.pinnedModels ?? []).length > 0}
					<PinnedModelList bind:selectedChatId {shiftKey} />
				{/if}

				{#if ($config?.features?.enable_notes ?? false) && ($user?.role === 'admin' || ($user?.permissions?.features?.notes ?? true)) && $pinnedNotes.length > 0}
					<Folder
						id="sidebar-pinned-notes"
						bind:open={showPinnedNotes}
						className="px-2 mt-0.5"
						name={$i18n.t('Notes')}
						chevron={false}
=======
				{#if ($models ?? []).length > 0 && (($settings?.pinnedModels ?? []).length > 0 || $config?.default_pinned_models)}
					<SidebarSection
						id="sidebar-models"
						bind:open={showPinnedModels}
						className="mt-0.5"
						name={$i18n.t('Models')}
>>>>>>> v0.11.0
						dragAndDrop={false}
						onAdd={async () => {
							const note = await createNoteHandler('New Note');
							if (note) {
								goto(`/notes/${note.id}`);
							}
						}}
						onAddLabel={$i18n.t('New Note')}
					>
<<<<<<< HEAD
						<div class="mt-0.5 pb-1.5">
							{#each $pinnedNotes as note (note.id)}
								<a
									class="w-full flex items-center gap-2.5 rounded-xl px-2.5 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900 transition group text-sm"
									href={`/notes/${note.id}`}
									on:click={() => {
										itemClickHandler();
									}}
									draggable="false"
								>
									<div class="self-center">
										<Note className="size-4" strokeWidth="2" />
									</div>
									<div class="flex-1 text-ellipsis line-clamp-1">
										{note.title}
									</div>
									<button
										class="invisible group-hover:visible self-center p-0.5 hover:bg-gray-200 dark:hover:bg-gray-800 rounded-lg transition"
										on:click|preventDefault|stopPropagation={async () => {
											await toggleNotePinnedStatusById(localStorage.token, note.id);
											const _pinnedNotes = await getPinnedNoteList(localStorage.token).catch(
												() => []
											);
											pinnedNotes.set(_pinnedNotes);
										}}
										aria-label={$i18n.t('Unpin')}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											fill="none"
											viewBox="0 0 24 24"
											stroke-width="2"
											stroke="currentColor"
											class="size-3.5"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												d="M6 18 18 6M6 6l12 12"
											/>
										</svg>
									</button>
								</a>
							{/each}
						</div>
					</Folder>
				{/if}

				{#if $config?.features?.enable_channels && ($user?.role === 'admin' || ($user?.permissions?.features?.channels ?? true))}
					<Folder
						className="px-2 mt-0.5"
=======
						<PinnedModelList bind:selectedChatId {shiftKey} />
					</SidebarSection>
				{/if}

				{#if ($config?.features?.enable_notes ?? false) && ($user?.role === 'admin' || ($user?.permissions?.features?.notes ?? true)) && $pinnedNotes.length > 0}
					<SidebarSection
						id="sidebar-pinned-notes"
						bind:open={showPinnedNotes}
						className="mt-0.5"
						name={$i18n.t('Notes')}
						dragAndDrop={false}
						onAdd={async () => {
							const note = await createNoteHandler('New Note');
							if (note) {
								goto(`/notes/${note.id}`);
							}
						}}
						onAddLabel={$i18n.t('New Note')}
					>
						<PinnedNoteList bind:selectedChatId />
					</SidebarSection>
				{/if}

				{#if $config?.features?.enable_channels && ($user?.role === 'admin' || ($user?.permissions?.features?.channels ?? true))}
					<SidebarSection
						id="sidebar-channels"
						bind:open={showChannels}
						className="mt-0.5"
>>>>>>> v0.11.0
						name={$i18n.t('Channels')}
						dragAndDrop={false}
						onAdd={async () => {
							if ($user?.role === 'admin') {
								await tick();

								setTimeout(() => {
									showCreateChannel = true;
								}, 0);
							}
						}}
						onAddLabel={$i18n.t('Create Channel')}
					>
						{#each $channels as channel}
							<ChannelItem
								{channel}
								onUpdate={async () => {
									await initChannels();
								}}
							/>
						{/each}
					</SidebarSection>
				{/if}

<<<<<<< HEAD
				{#if folders}
					<Folder
						className="px-2 mt-0.5"
=======
				{#if $config?.features?.enable_folders && ($user?.role === 'admin' || ($user?.permissions?.features?.folders ?? true))}
					<SidebarSection
						id="sidebar-folders"
						bind:open={showFolders}
						className="mt-0.5"
>>>>>>> v0.11.0
						name={$i18n.t('Folders')}
						onAdd={() => {
							showCreateFolderModal = true;
						}}
						onAddLabel={$i18n.t('New Folder')}
						on:drop={async (e) => {
							const { type, id, item } = e.detail;

							if (type === 'folder') {
								if (folders[id].parent_id === null) {
									return;
								}

								const res = await updateFolderParentIdById(localStorage.token, id, null).catch(
									(error) => {
										toast.error(`${error}`);
										return null;
									}
								);

								if (res) {
									await initFolders();
								}
							}
						}}
					>
						<Folders
							bind:folderRegistry
							{folders}
							{shiftKey}
							onFolderUnreadCounts={applyFolderUnreadCounts}
							onDelete={(folderId) => {
								selectedFolder.set(null);
								initChatList();
							}}
							on:update={() => {
								initChatList();
							}}
							on:import={(e) => {
								const { folderId, items } = e.detail;
								importChatHandler(items, false, folderId);
							}}
							on:change={async () => {
								initChatList();
							}}
						/>
					</SidebarSection>
				{/if}

<<<<<<< HEAD
				<Folder
					className="px-2 mt-0.5"
=======
				<SidebarSection
					id="sidebar-chats"
					className="mt-0.5"
>>>>>>> v0.11.0
					name={$i18n.t('Chats')}
					on:change={async (e) => {
						// Only clear selectedFolder, don't navigate away from current chat
						if ($selectedFolder !== null) {
							selectedFolder.set(null);
						}
					}}
					on:import={(e) => {
						importChatHandler(e.detail);
					}}
					on:drop={async (e) => {
						const { type, id, item } = e.detail;

						if (type === 'chat') {
							let chat = await getChatById(localStorage.token, id).catch((error) => {
								return null;
							});
							if (!chat && item) {
<<<<<<< HEAD
								chat = await importChat(
									localStorage.token,
									item.chat,
									item?.meta ?? {},
									false,
									null,
									item?.created_at ?? null,
									item?.updated_at ?? null
								);
=======
								if (!canImportChats) {
									toast.error($i18n.t('Access prohibited'));
									return;
								}

								chat = await importChats(localStorage.token, [
									{
										chat: item.chat,
										meta: item?.meta ?? {},
										pinned: false,
										folder_id: null,
										created_at: item?.created_at ?? null,
										updated_at: item?.updated_at ?? null
									}
								]);
>>>>>>> v0.11.0
							}

							if (chat) {
								if (chat.folder_id) {
									const res = await updateChatFolderIdById(localStorage.token, chat.id, null).catch(
										(error) => {
											toast.error(`${error}`);
											return null;
										}
									);

									folderRegistry[chat.folder_id]?.setFolderItems();
								}

								if (chat.pinned) {
									const res = await toggleChatPinnedStatusById(localStorage.token, chat.id);
								}

								initChatList();
							}
						} else if (type === 'folder') {
							if (folders[id].parent_id === null) {
								return;
							}

							const res = await updateFolderParentIdById(localStorage.token, id, null).catch(
								(error) => {
									toast.error(`${error}`);
									return null;
								}
							);

							if (res) {
								await initFolders();
							}
						}
					}}
				>
					<svelte:fragment slot="action">
						<Dropdown bind:show={showChatsMenu} align="end">
							<Tooltip content={$i18n.t('More')}>
								<button
									type="button"
									class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-100"
									aria-label={$i18n.t('More')}
									on:pointerup|stopPropagation
								>
									<MoreHorizontalIcon className="size-3.5" strokeWidth="2" />
								</button>
							</Tooltip>

							<div slot="content">
								<DropdownMenu className="min-w-[170px]">
									<button
										class="flex h-[1.6875rem] w-full items-center gap-2 rounded-xl px-2 text-[13px] select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
										on:click={markAllChatsReadHandler}
									>
										<CheckIcon className="size-3.5" />
										<div class="flex items-center">{$i18n.t('Mark all as read')}</div>
									</button>
								</DropdownMenu>
							</div>
						</Dropdown>
					</svelte:fragment>

					{#if $pinnedChats.length > 0}
						<div class="flex flex-col space-y-1 rounded-xl">
							<Folder
								className=""
								bind:open={showPinnedChat}
								on:change={(e) => {
									localStorage.setItem('showPinnedChat', e.detail);
								}}
								on:import={(e) => {
									importChatHandler(e.detail, true);
								}}
								on:drop={async (e) => {
									const { type, id, item } = e.detail;

<<<<<<< HEAD
									if (type === 'chat') {
										let chat = await getChatById(localStorage.token, id).catch((error) => {
											return null;
										});
										if (!chat && item) {
											chat = await importChat(
												localStorage.token,
												item.chat,
												item?.meta ?? {},
												false,
												null,
												item?.created_at ?? null,
												item?.updated_at ?? null
											);
=======
										if (type === 'chat') {
											let chat = await getChatById(localStorage.token, id).catch((error) => {
												return null;
											});
											if (!chat && item) {
												if (!canImportChats) {
													toast.error($i18n.t('Access prohibited'));
													return;
												}

												chat = await importChats(localStorage.token, [
													{
														chat: item.chat,
														meta: item?.meta ?? {},
														pinned: false,
														folder_id: null,
														created_at: item?.created_at ?? null,
														updated_at: item?.updated_at ?? null
													}
												]);
											}

											if (chat) {
												console.log(chat);
												if (chat.folder_id) {
													const res = await updateChatFolderIdById(
														localStorage.token,
														chat.id,
														null
													).catch((error) => {
														toast.error(`${error}`);
														return null;
													});
												}

												if (!chat.pinned) {
													const res = await toggleChatPinnedStatusById(localStorage.token, chat.id);
												}

												initChatList();
											}
>>>>>>> v0.11.0
										}

										if (chat) {
											if (chat.folder_id) {
												const res = await updateChatFolderIdById(
													localStorage.token,
													chat.id,
													null
												).catch((error) => {
													toast.error(`${error}`);
													return null;
												});
											}

											if (!chat.pinned) {
												const res = await toggleChatPinnedStatusById(localStorage.token, chat.id);
											}

											initChatList();
										}
									}
								}}
								name={$i18n.t('Pinned')}
							>
								<div
									class="ml-3 pl-1 mt-[1px] flex flex-col overflow-y-auto scrollbar-hidden border-s border-gray-100 dark:border-gray-900 text-gray-900 dark:text-gray-200"
								>
<<<<<<< HEAD
									{#each $pinnedChats as chat, idx (`pinned-chat-${chat?.id ?? idx}`)}
										<ChatItem
											className=""
											id={chat.id}
											title={chat.title}
											createdAt={chat.created_at}
											updatedAt={chat.updated_at}
											lastReadAt={chat.last_read_at}
											{shiftKey}
											selected={selectedChatId === chat.id}
											on:select={() => {
												selectedChatId = chat.id;
											}}
											on:unselect={() => {
												selectedChatId = null;
											}}
											on:change={async () => {
												initChatList();
											}}
											on:tag={(e) => {
												const { type, name } = e.detail;
												tagEventHandler(type, name, chat.id);
											}}
										/>
									{/each}
								</div>
							</Folder>
=======
									<div
										class="ml-3 pl-1 mt-[1px] flex flex-col overflow-y-auto scrollbar-hidden border-s border-gray-100 dark:border-gray-900 text-gray-700 dark:text-gray-300"
									>
										{#each $pinnedChats as chat, idx (`pinned-chat-${chat?.id ?? idx}`)}
											<ChatItem
												className=""
												id={chat.id}
												title={chat.title}
												createdAt={chat.created_at}
												updatedAt={chat.updated_at}
												lastReadAt={chat.last_read_at}
												active={chat.active ?? false}
												{shiftKey}
												selected={selectedChatId === chat.id}
												on:select={() => {
													selectedChatId = chat.id;
												}}
												on:unselect={() => {
													selectedChatId = null;
												}}
												on:change={async () => {
													initChatList();
												}}
												onReadStateChange={applyChatReadState}
												on:tag={(e) => {
													const { type, name } = e.detail;
													tagEventHandler(type, name, chat.id);
												}}
											/>
										{/each}
									</div>
								</Folder>
							</div>
>>>>>>> v0.11.0
						</div>
					{/if}

					<div class=" flex-1 flex flex-col overflow-y-auto scrollbar-hidden">
						<div class="pt-1.5">
							{#if $chats}
								{#each $chats as chat, idx (`chat-${chat?.id ?? idx}`)}
									{#if idx === 0 || (idx > 0 && chat.time_range !== $chats[idx - 1].time_range)}
										<div
											class="w-full pl-2.5 text-xs text-gray-500 dark:text-gray-500 font-normal {idx ===
											0
												? ''
												: 'pt-4'} pb-1"
										>
											{$i18n.t(chat.time_range)}
											<!-- localisation keys for time_range to be recognized from the i18next parser (so they don't get automatically removed):
							{$i18n.t('Today')}
							{$i18n.t('Yesterday')}
							{$i18n.t('Previous 7 days')}
							{$i18n.t('Previous 30 days')}
							{$i18n.t('January')}
							{$i18n.t('February')}
							{$i18n.t('March')}
							{$i18n.t('April')}
							{$i18n.t('May')}
							{$i18n.t('June')}
							{$i18n.t('July')}
							{$i18n.t('August')}
							{$i18n.t('September')}
							{$i18n.t('October')}
							{$i18n.t('November')}
							{$i18n.t('December')}
							-->
										</div>
									{/if}

									<ChatItem
										className=""
										id={chat.id}
										title={chat.title}
										createdAt={chat.created_at}
										updatedAt={chat.updated_at}
										lastReadAt={chat.last_read_at}
<<<<<<< HEAD
=======
										active={chat.active ?? false}
>>>>>>> v0.11.0
										{shiftKey}
										selected={selectedChatId === chat.id}
										on:select={() => {
											selectedChatId = chat.id;
										}}
										on:unselect={() => {
											selectedChatId = null;
										}}
										on:change={async () => {
											initChatList();
										}}
										onReadStateChange={applyChatReadState}
										on:tag={(e) => {
											const { type, name } = e.detail;
											tagEventHandler(type, name, chat.id);
										}}
									/>
								{/each}

								{#if chatListReady && !allChatsLoaded}
									<Loader
										on:visible={(e) => {
											if (!chatListLoading) {
												loadMoreChats();
											}
										}}
									>
										<div
											class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2"
										>
											<Spinner className=" size-4" />
											<div class=" ">{$i18n.t('Loading...')}</div>
										</div>
									</Loader>
								{/if}
							{:else}
								<div
									class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2"
								>
									<Spinner className=" size-4" />
									<div class=" ">{$i18n.t('Loading...')}</div>
								</div>
							{/if}
						</div>
					</div>
				</SidebarSection>
			</div>

			<div class="px-1 pt-1 pb-1.5 sticky bottom-0 z-10 -mt-2 sidebar">
				<div
					class=" sidebar-bg-gradient-to-t bg-linear-to-t from-gray-50 dark:from-gray-950 to-transparent from-50% pointer-events-none absolute inset-0 -z-10 -mt-6"
				></div>
				<div class="flex flex-col">
					{#if $user !== undefined && $user !== null}
						<UserMenu
							role={$user?.role}
							profile={$config?.features?.enable_user_status ?? true}
							className="w-[calc(var(--sidebar-width)-1rem)]"
						>
							<button
								type="button"
<<<<<<< HEAD
								class=" flex items-center rounded-2xl py-2 px-1.5 w-full hover:bg-gray-100/50 dark:hover:bg-gray-900/50 transition"
								aria-label={$i18n.t('User menu')}
							>
								<div class="self-center mr-3 relative flex-shrink-0">
									<img
										src={$user?.profile_image_url}
										class=" size-6 object-cover rounded-full"
=======
								class=" flex items-center rounded-xl py-1.5 px-1.5 w-full hover:bg-gray-50 dark:hover:bg-gray-900 transition"
								aria-label={$i18n.t('User menu')}
							>
								<div class=" self-center mr-3 relative flex-shrink-0">
									<img
										src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
										class="size-5.5 object-cover rounded-full"
>>>>>>> v0.11.0
										alt={$i18n.t('Open User Profile Menu')}
										aria-label={$i18n.t('Open User Profile Menu')}
									/>

									{#if $config?.features?.enable_user_status}
										<div class="absolute -bottom-0.5 -right-0.5">
											<span class="relative flex size-2.5">
												<span
													class="relative inline-flex size-2.5 rounded-full {true
														? 'bg-green-500'
														: 'bg-gray-300 dark:bg-gray-700'} border-2 border-white dark:border-gray-900"
												></span>
											</span>
										</div>
									{/if}
								</div>
<<<<<<< HEAD
								<div class="flex flex-col flex-1 min-w-0">
									<div class="flex font-medium truncate">{$user?.name}</div>

									{#if myUsageLoading}
										<div class="text-xs text-gray-400 dark:text-gray-500 animate-pulse">
											{$i18n.t('Loading...')}
										</div>
									{:else if myUsage !== null}
										<Tooltip
											placement="top"
											interactive={true}
											content={myUsageTooltip}
											tippyOptions={{ allowHTML: true }}
										>
											<div class="text-xs text-gray-500 dark:text-gray-400 cursor-default">
												{#if isInternalUser($billingStatus?.plan_tier)}
													{$i18n.t('This month')}:
													<span class="font-medium text-gray-700 dark:text-gray-300"
														>€{(myUsage.total_cost_eur ?? 0).toFixed(2)}</span
													>
												{:else if $billingStatus?.plan_tier === 'team' || $billingStatus?.plan_tier === 'team_member'}
													{$i18n.t('Your usage')}:
													<span class="font-medium text-gray-700 dark:text-gray-300"
														>{(myUsage.credits_used ?? 0).toLocaleString()} {$i18n.t('cr')}</span
													>
												{:else}
													{$i18n.t('This month')}:
													<span class="font-medium text-gray-700 dark:text-gray-300"
														>{myUsage.credits_used ?? 0} / {myUsage.credits_balance}
														{$i18n.t('credits')}</span
													>
												{/if}
											</div>
										</Tooltip>
										{#if ($billingStatus?.plan_tier === 'team_member' || $billingStatus?.plan_tier === 'team') && $billingStatus?.credits_remaining !== undefined}
											<div class="text-xs text-gray-400 dark:text-gray-500 text-left">
												{$i18n.t('Left in pool')}: <span class="font-medium text-gray-700 dark:text-gray-300">{($billingStatus.credits_remaining).toLocaleString()} {$i18n.t('cr')}</span>
											</div>
										{/if}
									{:else}
										<div class="text-xs text-gray-400 dark:text-gray-500 text-left">
											{$i18n.t('Usage unavailable')}
										</div>
									{/if}
								</div>
=======
								<div class=" self-center font-normal truncate">{$user?.name}</div>
>>>>>>> v0.11.0
							</button>
						</UserMenu>
					{/if}
				</div>
			</div>
		</div>
	</div>

	{#if !$mobile}
		<!-- A resizable separator is a sanctioned WAI-ARIA pattern (role="separator"
		     + tabindex + arrow-key resize + aria-value*), but Svelte's linter treats
		     `separator` as always non-interactive — hence the ignores below. -->
		<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
		<!-- svelte-ignore a11y-no-noninteractive-tabindex -->
		<div
			class="relative flex items-center justify-center group border-l border-gray-50 dark:border-gray-850/30 hover:border-gray-200 dark:hover:border-gray-800 transition z-20"
			id="sidebar-resizer"
			on:mousedown={resizeStartHandler}
			on:keydown={resizeKeyHandler}
			role="separator"
			aria-orientation="vertical"
			aria-valuenow={$sidebarWidth ?? 260}
			aria-valuemin={MIN_WIDTH}
			aria-valuemax={MAX_WIDTH}
			aria-label={$i18n.t('Resize sidebar')}
			tabindex="0"
		>
			<div
				class=" absolute -left-1.5 -right-1.5 -top-0 -bottom-0 z-20 cursor-col-resize bg-transparent"
			></div>
		</div>
	{/if}
{/if}
