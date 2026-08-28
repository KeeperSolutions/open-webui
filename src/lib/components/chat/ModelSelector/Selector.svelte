<script lang="ts">
	import { marked } from 'marked';
	import Fuse from 'fuse.js';

	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import Spinner from '$lib/components/common/Spinner.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
<<<<<<< HEAD
=======
	import { flyAndScale } from '$lib/utils/transitions';
>>>>>>> v0.11.0

	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';

	import { deleteModel, getOllamaVersion, pullModel } from '$lib/apis/ollama';
<<<<<<< HEAD
	import { unloadModel } from '$lib/apis';

	import { user, MODEL_DOWNLOAD_POOL, models, mobile, settings, config, theme } from '$lib/stores';
	import type { Model } from '$lib/stores';
=======
	import { deleteModelById } from '$lib/apis/models';
	import { unloadModel } from '$lib/apis';

	import {
		user,
		MODEL_DOWNLOAD_POOL,
		models,
		temporaryChatEnabled,
		settings,
		config,
		showSettings
	} from '$lib/stores';
>>>>>>> v0.11.0
	import { toast } from 'svelte-sonner';
	import { splitStream } from '$lib/utils';
	import { resolveTheme } from '$lib/utils/theme';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { getModels } from '$lib/apis';
	import { getFeaturedModels } from '$lib/apis/configs';
	import { updateUserSettings } from '$lib/apis/users';

	import HgIconChevronRight from '$lib/components/icons/HgIconChevronRight.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
<<<<<<< HEAD
=======
	import Switch from '$lib/components/common/Switch.svelte';
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';
	import Keyframes from '$lib/components/icons/Keyframes.svelte';
	import TagSelector from '$lib/components/workspace/common/TagSelector.svelte';
>>>>>>> v0.11.0

	import ModelItem from './ModelItem.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let id = '';
	export let value: string | null = '';
	export let values: string[] | null = null;
	export let compareEnabled = false;
	export let multipleEnabled = false;
	export let disabled = false;
	export let placeholder = $i18n.t('Select a model');
	export let searchEnabled = true;
	export let searchPlaceholder = $i18n.t('Search a model');
	export let selectionOnly = false;
	export let includeHidden = false;

	export let items: {
		label: string;
		value: string;
		model: Model;
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		[key: string]: any;
	}[] = [];

<<<<<<< HEAD
=======
	export let className = 'w-[20rem]';
>>>>>>> v0.11.0
	export let triggerClassName = 'text-lg';
	export let placement: 'top' | 'bottom' | 'auto' = 'bottom';
	export let align: 'start' | 'end' = 'start';
	export let showSetDefault = false;
	export let onSetDefault: () => Promise<void> | void = () => {};

	export let pinModelHandler: (modelId: string) => void = () => {};

	let show = false;
	let triggerElement: HTMLElement | null = null;
	let contentElement: HTMLElement | null = null;
	let panelElement: HTMLElement | null = null;
	let dropdownPosition = { top: 0, left: 0, maxHeight: undefined as number | undefined };
	let positionFrame: number | undefined;
	let settleTimers: number[] = [];

	const portal = (node: HTMLElement) => {
		document.body.appendChild(node);
		return {
			destroy() {
				node.remove();
			}
		};
	};

	const measureContent = () => {
		if (!contentElement) return { width: 0, height: 0 };

		const previousMaxHeight = panelElement?.style.maxHeight;
		if (panelElement) panelElement.style.maxHeight = '';
		const rect = contentElement.getBoundingClientRect();
		if (panelElement && previousMaxHeight !== undefined) {
			panelElement.style.maxHeight = previousMaxHeight;
		}

		return { width: rect.width, height: rect.height };
	};

	const visualViewportRect = () => {
		const viewport = window.visualViewport;
		return {
			left: viewport?.offsetLeft ?? 0,
			top: viewport?.offsetTop ?? 0,
			width: viewport?.width ?? window.innerWidth,
			height: viewport?.height ?? window.innerHeight
		};
	};

	const updatePosition = () => {
		if (!show || !triggerElement) return;
		const rect = triggerElement.getBoundingClientRect();
		const { width: contentWidth, height: contentHeight } = measureContent();
		const viewport = visualViewportRect();
		const viewportRight = viewport.left + viewport.width;
		const viewportBottom = viewport.top + viewport.height;
		const pad = 8;
		const gap = 2;
		const spaceBelow = viewportBottom - rect.bottom - gap - pad;
		const spaceAbove = rect.top - viewport.top - gap - pad;
		const preferredLeft = align === 'end' && contentWidth ? rect.right - contentWidth : rect.left;
		const maxLeft = contentWidth ? viewportRight - contentWidth - pad : preferredLeft;
		const resolvedPlacement =
			placement === 'auto'
				? contentHeight && spaceBelow < contentHeight && spaceAbove > spaceBelow
					? 'top'
					: 'bottom'
				: placement;
		const availableHeight = resolvedPlacement === 'top' ? spaceAbove : spaceBelow;
		const constrainedHeight =
			contentHeight && availableHeight >= 0
				? Math.min(contentHeight, availableHeight)
				: contentHeight;
		const top =
			resolvedPlacement === 'top' && contentHeight
				? rect.top - constrainedHeight - gap
				: rect.bottom + gap;

		dropdownPosition = {
			top: Math.max(viewport.top + pad, Math.min(top, viewportBottom - pad - constrainedHeight)),
			left: Math.max(viewport.left + pad, Math.min(preferredLeft, maxLeft)),
			maxHeight:
				contentHeight && availableHeight >= 0 && contentHeight > availableHeight
					? Math.max(0, availableHeight)
					: undefined
		};
	};

	const schedulePositionUpdate = () => {
		if (positionFrame != null) cancelAnimationFrame(positionFrame);
		positionFrame = requestAnimationFrame(() => {
			positionFrame = undefined;
			updatePosition();
		});
	};

	const scheduleSettledPositionUpdates = () => {
		for (const timer of settleTimers) window.clearTimeout(timer);
		settleTimers = [];
		schedulePositionUpdate();
		for (const delay of [50, 150, 300]) {
			settleTimers.push(window.setTimeout(schedulePositionUpdate, delay));
		}
	};

	const handleScroll = (event: Event) => {
		if (event.target instanceof Node && contentElement?.contains(event.target)) return;
		schedulePositionUpdate();
	};

	const toggleOpen = async () => {
		show = !show;
		if (show) {
			searchValue = '';
			listScrollTop = 0;
			resetView();
			updatePosition();
			await tick();
			updatePosition();
			window.setTimeout(() => document.getElementById('model-search-input')?.focus(), 0);
		} else {
			document.getElementById(`model-selector-${id}-button`)?.blur();
		}
	};

	const handlePointerDown = (e: PointerEvent) => {
		if (!show) return;
		const target = e.target as Node;
		if (
			(triggerElement && triggerElement.contains(target)) ||
			(contentElement && contentElement.contains(target)) ||
			((target as HTMLElement).closest?.('.model-selector-child-menu') ?? false)
		) {
			return;
		}
		show = false;
		document.getElementById(`model-selector-${id}-button`)?.blur();
	};

	const handleKeydown = (e: KeyboardEvent) => {
		if (show && e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			show = false;
			document.getElementById(`model-selector-${id}-button`)?.blur();
		}
	};

	let tags = [];

<<<<<<< HEAD
	let selectedModel: (typeof items)[number] | null = null;
	$: selectedModel = items.find((item) => item.value === value) ?? null;

	$: resolvedTheme = resolveTheme($theme);

	$: selectedNameHead = (() => {
		if (!selectedModel) return '';
		const label = (selectedModel.label ?? '').trim();
		const spaceIdx = label.indexOf(' ');
		return spaceIdx === -1 ? label : label.slice(0, spaceIdx);
	})();

	$: selectedNameTail = (() => {
		if (!selectedModel) return '';
		const label = (selectedModel.label ?? '').trim();
		const spaceIdx = label.indexOf(' ');
		return spaceIdx === -1 ? '' : label.slice(spaceIdx + 1).trim();
	})();

	$: isCurrentModelDefault = !!value && ($settings?.models?.[0] ?? '') === value;

	const toggleDefaultModel = async () => {
		if (!value) return;
		const newDefault = isCurrentModelDefault ? '' : value;
		await settings.set({ ...$settings, models: newDefault ? [newDefault] : [] });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};
=======
	let selectedModel = '';
	$: selectedValues = values ?? (value ? [value] : []);
	$: primaryValue = selectedValues[0] ?? value ?? '';
	$: selectedModel = items.find((item) => item.value === primaryValue) ?? '';
	$: selectedCount = selectedValues.filter(Boolean).length;
	$: triggerLabel = selectedModel
		? compareEnabled && selectedCount > 1
			? `${selectedModel.label} +${selectedCount - 1}`
			: selectedModel.label
		: placeholder;
>>>>>>> v0.11.0

	let searchValue = '';

	let selectedTag = '';
	let selectedConnectionType = '';
	let selectedFilter = '';
	let modelFilterItems = [];

	let rawFeaturedModels: {
		model_id: string;
		provider_name: string;
		featured_name: string;
		tags: [string, string, string];
		order: number;
	}[] = [];

	$: availableIds = new Set(
		items.filter((item) => !(item.model?.info?.meta?.hidden ?? false)).map((item) => item.value)
	);

	$: featuredModels =
		rawFeaturedModels.length > 0
			? [...rawFeaturedModels]
					.filter((m) => availableIds.has(m.model_id))
					.sort((a, b) => a.order - b.order)
			: [];

	// Map a featured entry onto the `item` shape ModelItem consumes, reusing the
	// real backing model (logo, menu) while overriding the display name/tags with
	// the curated featured values. Only entries with a backing item are rendered
	// (featuredModels is already filtered to availableIds), so the lookup is safe.
	const featuredToItem = (entry: (typeof rawFeaturedModels)[number]) => {
		const backing = items.find((item) => item.value === entry.model_id);
		const featuredTags = (entry.tags ?? []).filter(Boolean).map((name) => ({ name }));

		return {
			...backing,
			value: entry.model_id,
			label: entry.featured_name || backing?.label,
			providerName: entry.provider_name || undefined,
			model: {
				...backing?.model,
				id: entry.model_id,
				tags: featuredTags.length > 0 ? featuredTags : (backing?.model?.tags ?? [])
			}
		};
	};

	let ollamaVersion = null;
	let selectedModelIdx = 0;

	const fuse = new Fuse(
		items.map((item) => {
			const _item = {
				...item,
				modelName: item.model?.name,
				tags: (item.model?.tags ?? []).map((tag) => tag.name).join(' '),
				desc: item.model?.info?.meta?.description
			};
			return _item;
		}),
		{
			keys: ['value', 'tags', 'modelName'],
			threshold: 0.4
		}
	);

	const updateFuse = () => {
		if (fuse) {
			fuse.setCollection(
				items.map((item) => {
					const _item = {
						...item,
						modelName: item.model?.name,
						tags: (item.model?.tags ?? []).map((tag) => tag.name).join(' '),
						desc: item.model?.info?.meta?.description
					};
					return _item;
				})
			);
		}
	};

	$: if (items) {
		updateFuse();
	}

	$: filteredItems = (
		searchValue
			? fuse
					.search(searchValue)
					.map((e) => {
						return e.item;
					})
					.filter((item) => {
						if (selectedTag === '') {
							return true;
						}

						return (item.model?.tags ?? [])
							.map((tag) => tag.name.toLowerCase())
							.includes(selectedTag.toLowerCase());
					})
					.filter((item) => {
						if (selectedConnectionType === '') {
							return true;
						} else if (selectedConnectionType === 'local') {
							return item.model?.connection_type === 'local';
						} else if (selectedConnectionType === 'external') {
							return item.model?.connection_type === 'external';
						} else if (selectedConnectionType === 'direct') {
							return item.model?.direct;
						} else if (selectedConnectionType === 'featured') {
							return false;
						}
					})
			: items
					.filter((item) => {
						if (selectedTag === '') {
							return true;
						}
						return (item.model?.tags ?? [])
							.map((tag) => tag.name.toLowerCase())
							.includes(selectedTag.toLowerCase());
					})
					.filter((item) => {
						if (selectedConnectionType === '') {
							return true;
						} else if (selectedConnectionType === 'local') {
							return item.model?.connection_type === 'local';
						} else if (selectedConnectionType === 'external') {
							return item.model?.connection_type === 'external';
						} else if (selectedConnectionType === 'direct') {
							return item.model?.direct;
						} else if (selectedConnectionType === 'featured') {
							return false;
						}
					})
	).filter((item) => includeHidden || !(item.model?.info?.meta?.hidden ?? false));

	$: if (searchValue && selectedConnectionType === 'featured') {
		selectedConnectionType = '';
	}

	$: if (selectedTag || selectedConnectionType) {
		resetView();
	}

	$: modelFilterItems = [
		...(items.find((item) => item.model?.connection_type === 'local')
			? [{ value: 'connection:local', label: $i18n.t('Local') }]
			: []),
		...(items.find((item) => item.model?.connection_type === 'external')
			? [{ value: 'connection:external', label: $i18n.t('External') }]
			: []),
		...(items.find((item) => item.model?.direct)
			? [{ value: 'connection:direct', label: $i18n.t('Direct') }]
			: []),
		...tags.map((tag) => ({ value: `tag:${tag}`, label: tag }))
	];

	$: selectedFilter = selectedConnectionType
		? `connection:${selectedConnectionType}`
		: selectedTag
			? `tag:${selectedTag}`
			: '';

	const setModelFilter = (filterValue: string) => {
		if (!filterValue) {
			selectedConnectionType = '';
			selectedTag = '';
		} else if (filterValue.startsWith('connection:')) {
			selectedConnectionType = filterValue.replace('connection:', '');
			selectedTag = '';
		} else if (filterValue.startsWith('tag:')) {
			selectedConnectionType = '';
			selectedTag = filterValue.replace('tag:', '');
		}
	};

	const resetView = async () => {
		await tick();

<<<<<<< HEAD
		const isFeatured = selectedConnectionType === 'featured';
		const activeList = isFeatured ? featuredModels : filteredItems;
		const selectedInActive = isFeatured
			? activeList.findIndex((entry) => entry.model_id === value)
			: activeList.findIndex((item) => item.value === value);
=======
		const selectedInFiltered = filteredItems.findIndex((item) => item.value === primaryValue);
>>>>>>> v0.11.0

		selectedModelIdx = selectedInActive >= 0 ? selectedInActive : 0;

		// Set the virtual scroll position so the selected item is rendered and centered
		const targetScrollTop = Math.max(0, selectedModelIdx * ITEM_HEIGHT - 128 + ITEM_HEIGHT / 2);
		listScrollTop = targetScrollTop;

		await tick();

		if (listContainer) {
			listContainer.scrollTop = targetScrollTop;
		}

		await tick();
		const item = document.querySelector(`[data-arrow-selected="true"]`);
		item?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
		schedulePositionUpdate();
	};

	const setCompareEnabled = (enabled: boolean) => {
		compareEnabled = enabled;

		if (!enabled && values) {
			values = [primaryValue || selectedValues[0] || ''];
			value = values[0];
		}
	};

	const selectItem = (item, index: number) => {
		selectedModelIdx = index;

		if (values) {
			if (compareEnabled) {
				const nextValues = selectedValues.includes(item.value)
					? selectedValues.length > 1
						? selectedValues.filter((selectedValue) => selectedValue !== item.value)
						: selectedValues
					: [...selectedValues.filter(Boolean), item.value];

				values = nextValues.length ? nextValues : [item.value];
				value = values[0];
				return;
			}

			values = [item.value];
			value = item.value;
			show = false;
			return;
		}

		value = item.value;
		show = false;
	};

	const setDefaultHandler = async () => {
		await onSetDefault();
	};

	const pullModelHandler = async () => {
		const sanitizedModelTag = searchValue.trim().replace(/^ollama\s+(run|pull)\s+/, '');

		console.log($MODEL_DOWNLOAD_POOL);
		if ($MODEL_DOWNLOAD_POOL[sanitizedModelTag]) {
			toast.error(
				$i18n.t(`Model '{{modelTag}}' is already in queue for downloading.`, {
					modelTag: sanitizedModelTag
				})
			);
			return;
		}
		if (Object.keys($MODEL_DOWNLOAD_POOL).length === 3) {
			toast.error(
				$i18n.t('Maximum of 3 models can be downloaded simultaneously. Please try again later.')
			);
			return;
		}

		const [res, controller] = await pullModel(localStorage.token, sanitizedModelTag, '0').catch(
			(error) => {
				toast.error(`${error}`);
				return null;
			}
		);

		if (res) {
			const reader = res.body
				.pipeThrough(new TextDecoderStream())
				.pipeThrough(splitStream('\n'))
				.getReader();

			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL,
				[sanitizedModelTag]: {
					...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
					abortController: controller,
					reader,
					done: false
				}
			});

			while (true) {
				try {
					const { value, done } = await reader.read();
					if (done) break;

					let lines = value.split('\n');

					for (const line of lines) {
						if (line !== '') {
							let data = JSON.parse(line);
							console.log(data);
							if (data.error) {
								throw data.error;
							}
							if (data.detail) {
								throw data.detail;
							}

							if (data.status) {
								if (data.digest) {
									let downloadProgress = 0;
									if (data.completed) {
										downloadProgress = Math.round((data.completed / data.total) * 1000) / 10;
									} else {
										downloadProgress = 100;
									}

									MODEL_DOWNLOAD_POOL.set({
										...$MODEL_DOWNLOAD_POOL,
										[sanitizedModelTag]: {
											...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
											pullProgress: downloadProgress,
											digest: data.digest
										}
									});
								} else {
									toast.success(data.status);

									MODEL_DOWNLOAD_POOL.set({
										...$MODEL_DOWNLOAD_POOL,
										[sanitizedModelTag]: {
											...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
											done: data.status === 'success'
										}
									});
								}
							}
						}
					}
				} catch (error) {
					console.log(error);
					if (typeof error !== 'string') {
						error = error.message;
					}

					toast.error(`${error}`);
					// opts.callback({ success: false, error, modelName: opts.modelName });
					break;
				}
			}

			if ($MODEL_DOWNLOAD_POOL[sanitizedModelTag].done) {
				toast.success(
					$i18n.t(`Model '{{modelName}}' has been successfully downloaded.`, {
						modelName: sanitizedModelTag
					})
				);

				models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
			} else {
				toast.error($i18n.t('Download canceled'));
			}

			delete $MODEL_DOWNLOAD_POOL[sanitizedModelTag];

			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL
			});
		}
	};

	const setOllamaVersion = async () => {
		ollamaVersion = await getOllamaVersion(localStorage.token).catch((error) => false);
	};

	onMount(() => {
		if (items) {
			tags = items
				.filter((item) => includeHidden || !(item.model?.info?.meta?.hidden ?? false))
				.flatMap((item) => item.model?.tags ?? [])
				.map((tag) => tag.name.toLowerCase());
			// Remove duplicates and sort
			tags = Array.from(new Set(tags)).sort((a, b) => a.localeCompare(b));
		}

<<<<<<< HEAD
		try {
			const config = await getFeaturedModels(localStorage.token);
			const raw = config?.FEATURED_MODELS;
			if (Array.isArray(raw) && raw.length > 0) {
				rawFeaturedModels = raw;
			}
		} catch {
			// non-blocking — featured models are best-effort
		}
=======
		window.addEventListener('scroll', handleScroll, true);
		window.visualViewport?.addEventListener('resize', scheduleSettledPositionUpdates);
		window.visualViewport?.addEventListener('scroll', schedulePositionUpdate);

		return () => {
			if (positionFrame != null) cancelAnimationFrame(positionFrame);
			for (const timer of settleTimers) window.clearTimeout(timer);
			window.removeEventListener('scroll', handleScroll, true);
			window.visualViewport?.removeEventListener('resize', scheduleSettledPositionUpdates);
			window.visualViewport?.removeEventListener('scroll', schedulePositionUpdate);
		};
>>>>>>> v0.11.0
	});

	$: if (show && !selectionOnly) {
		setOllamaVersion();
	}

	const cancelModelPullHandler = async (model: string) => {
		const { reader, abortController } = $MODEL_DOWNLOAD_POOL[model];
		if (abortController) {
			abortController.abort();
		}
		if (reader) {
			await reader.cancel();
			delete $MODEL_DOWNLOAD_POOL[model];
			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL
			});
			await deleteModel(localStorage.token, model);
			toast.success($i18n.t('{{model}} download has been canceled', { model: model }));
		}
	};

	const unloadModelHandler = async (model: string) => {
		const res = await unloadModel(localStorage.token, model).catch((error) => {
			toast.error($i18n.t('Error unloading model: {{error}}', { error }));
		});

		if (res) {
			toast.success($i18n.t('Model unloaded successfully'));
			models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);
		}
	};

	let showDeleteConfirm = false;
	let deleteModelTarget: any = null;

	const deleteModelHandler = async (model: any) => {
		deleteModelTarget = model;
		showDeleteConfirm = true;
	};

	const confirmDeleteModel = async () => {
		const model = deleteModelTarget;
		if (!model) return;

<<<<<<< HEAD
		const res = await deleteModel(localStorage.token, model.id).catch((error) => {
			toast.error($i18n.t('Error deleting model: {{error}}', { error }));
		});

		if (res) {
			// $i18n.t('Model {{modelId}} not found')
=======
		let success = false;

		if (model?.info?.base_model_id) {
			// Workspace model: only delete the workspace model record, not the underlying base model
			const res = await deleteModelById(localStorage.token, model.id).catch((error) => {
				toast.error($i18n.t('Error deleting model: {{error}}', { error }));
				return null;
			});
			success = !!res;
		} else {
			// Base Ollama model: delete from Ollama directly
			const res = await deleteModel(localStorage.token, model.id).catch((error) => {
				toast.error($i18n.t('Error deleting model: {{error}}', { error }));
				return null;
			});
			success = !!res;
		}

		if (success) {
>>>>>>> v0.11.0
			toast.success(
				$i18n.t('Model {{modelName}} deleted successfully', { modelName: model.name ?? model.id })
			);

			// If the deleted model was selected, clear the selection
			if (value === model.id) {
				value = '';
			}

			models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);
		}

		deleteModelTarget = null;
	};

<<<<<<< HEAD
	const ITEM_HEIGHT = 42;
=======
	const ITEM_HEIGHT = 32;
>>>>>>> v0.11.0
	const OVERSCAN = 10;

	let listScrollTop = 0;
	let listContainer;
	let listViewportHeight = 288;

	const trackListViewport = (node: HTMLElement) => {
		const updateHeight = () => {
			listViewportHeight = node.clientHeight || 288;
		};

		updateHeight();

		if (!('ResizeObserver' in window)) {
			return { destroy() {} };
		}

		const observer = new ResizeObserver(updateHeight);
		observer.observe(node);

		return {
			destroy() {
				observer.disconnect();
			}
		};
	};

	$: visibleStart = Math.max(0, Math.floor(listScrollTop / ITEM_HEIGHT) - OVERSCAN);
	$: visibleEnd = Math.min(
		filteredItems.length,
		Math.ceil((listScrollTop + listViewportHeight) / ITEM_HEIGHT) + OVERSCAN
	);
</script>

<ConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete Model')}
	message={$i18n.t('Are you sure you want to delete **{{modelName}}**?', {
		modelName: deleteModelTarget?.name ?? deleteModelTarget?.id ?? ''
	})}
	on:confirm={() => {
		confirmDeleteModel();
	}}
/>

<<<<<<< HEAD
<DropdownMenu.Root
	bind:open={show}
	onOpenChange={async () => {
		searchValue = '';
		listScrollTop = 0;
		const isFeaturedSelected = featuredModels.some((m) => m.model_id === value);
		selectedConnectionType =
			isFeaturedSelected || (!value && featuredModels.length > 0) ? 'featured' : '';
		window.setTimeout(() => document.getElementById('model-search-input')?.focus(), 0);
		resetView();
	}}
	onOpenChangeComplete={(open) => {
		if (!open) {
			// Replaces the old closeFocus={false} behavior - prevent focus jump back to trigger
			document.getElementById(`model-selector-${id}-button`)?.blur();
		}
	}}
>
	<DropdownMenu.Trigger
=======
<svelte:window
	on:pointerdown={handlePointerDown}
	on:keydown={handleKeydown}
	on:resize={scheduleSettledPositionUpdates}
/>

<div class="relative w-full">
	<button
		bind:this={triggerElement}
>>>>>>> v0.11.0
		class="relative w-full {($settings?.highContrastMode ?? false)
			? ''
			: 'outline-hidden focus:outline-hidden'}"
		aria-label={selectedModel
			? $i18n.t('Selected model: {{modelName}}', { modelName: triggerLabel })
			: placeholder}
		aria-haspopup="listbox"
		aria-expanded={show}
		id="model-selector-{id}-button"
		type="button"
<<<<<<< HEAD
	>
		<div
			class="flex w-full items-center justify-between gap-2 px-3 py-2 rounded-hg-md border border-hg-border-subtle dark:border-gray-800 bg-transparent text-left truncate {triggerClassName}"
			role="presentation"
=======
		{disabled}
		on:click={toggleOpen}
	>
		<div
			class="flex w-full min-w-0 text-left px-0.5 bg-transparent {triggerClassName} justify-between {($settings?.highContrastMode ??
			false)
				? 'dark:placeholder-gray-100 placeholder-gray-800'
				: 'placeholder-gray-400'}"
>>>>>>> v0.11.0
			on:mouseenter={async () => {
				models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
			}}
		>
<<<<<<< HEAD
			<div class="flex items-center gap-2 min-w-0">
				{#if selectedModel}
					<img
						src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${selectedModel.value}&theme=${resolvedTheme}&lang=${$i18n.language}`}
						alt=""
						class="size-5 rounded-full shrink-0"
						loading="lazy"
					/>
					<span class="font-hg-body font-semibold text-base truncate">
						<span class="text-hg-text-primary dark:text-gray-100">{selectedNameHead}</span
						>{#if selectedNameTail}<span class="text-hg-text-tertiary dark:text-gray-500"
								>{' '}– {selectedNameTail}</span
							>{/if}
					</span>
				{:else}
					<span class="font-hg-body text-hg-text-tertiary dark:text-gray-400 truncate"
						>{placeholder}</span
					>
				{/if}
			</div>
			<HgIconChevronRight class="shrink-0 size-5 text-hg-orange dark:text-gray-400 rotate-90" />
=======
			<span class="min-w-0 flex-1 truncate">{triggerLabel}</span>
			<ChevronDown className="ml-1 size-2.5 shrink-0 self-center" strokeWidth="2.5" />
>>>>>>> v0.11.0
		</div>
	</button>

<<<<<<< HEAD
	<!-- Portal keeps the menu's position:fixed anchored to the viewport. Without it
		the menu resolves against the nearest ancestor with a transform / filter /
		container-type instead — the chat column already carries Tailwind's
		`@container`, so any future move of the navbar under such an ancestor would
		silently push the menu off-screen (see PiiMaskedCard for that exact bug).
		z-40 stays below the app's modals (z-9999, also body-mounted). -->
	<DropdownMenu.Portal>
		<DropdownMenu.Content
			class="model-selector-panel z-40 {$mobile
				? 'w-full'
				: 'w-[400px]'} max-w-[calc(100vw-1rem)] rounded-2xl bg-white dark:bg-gray-850 dark:text-white border border-hg-border-subtle dark:border-gray-800 shadow-lg outline-hidden overflow-hidden"
			side="bottom"
			align={$mobile ? 'center' : 'start'}
			sideOffset={2}
			alignOffset={-1}
		>
			<slot>
				{#if searchEnabled}
					<div class="p-3 border-b border-hg-border-subtle dark:border-gray-800">
						<div
							class="flex items-center gap-2 h-[44px] px-2 rounded-hg-md border border-hg-border dark:border-gray-700 bg-hg-bg-surface dark:bg-gray-900"
						>
							<Search
								className="size-5 shrink-0 text-hg-text-tertiary dark:text-gray-500"
								strokeWidth="2"
							/>
							<input
								id="model-search-input"
								bind:value={searchValue}
								class="flex-1 text-sm bg-transparent outline-hidden text-hg-text-primary dark:text-gray-100 placeholder:text-hg-text-tertiary dark:placeholder:text-gray-500"
								placeholder={searchPlaceholder}
								autocomplete="off"
								aria-label={$i18n.t('Search In Models')}
								on:keydown={(e) => {
									const isFeatured = selectedConnectionType === 'featured';
									const activeList = isFeatured ? featuredModels : filteredItems;
									if (e.code === 'Enter' && activeList.length > 0) {
										value = isFeatured
											? featuredModels[selectedModelIdx].model_id
											: filteredItems[selectedModelIdx].value;
										show = false;
										return;
									} else if (e.code === 'ArrowDown') {
										e.stopPropagation();
										selectedModelIdx = Math.min(selectedModelIdx + 1, activeList.length - 1);
									} else if (e.code === 'ArrowUp') {
										e.stopPropagation();
										selectedModelIdx = Math.max(selectedModelIdx - 1, 0);
									} else {
										selectedModelIdx = 0;
									}

									const item = document.querySelector(`[data-arrow-selected="true"]`);
									item?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
								}}
							/>
						</div>
					</div>
				{/if}

				{#if tags && items.filter((item) => !(item.model?.info?.meta?.hidden ?? false)).length > 0}
					<div
						class="flex gap-1 p-3 border-b border-hg-border-subtle dark:border-gray-800 overflow-x-auto scrollbar-none"
						on:wheel={(e) => {
							if (e.deltaY !== 0) {
								e.preventDefault();
								e.currentTarget.scrollLeft += e.deltaY;
							}
						}}
					>
						<div class="flex gap-1 w-fit whitespace-nowrap" bind:this={tagsContainerElement}>
							{#if featuredModels.length > 0 && !searchValue}
								<button
									class="shrink-0 h-8 px-3 rounded-full text-xs font-hg-body outline-none transition capitalize {selectedConnectionType ===
									'featured'
										? 'bg-hg-text-primary dark:bg-gray-100 text-white dark:text-gray-900'
										: 'bg-hg-bg-muted dark:bg-gray-800 border border-hg-border dark:border-gray-700 text-hg-text-tertiary dark:text-gray-400 hover:text-hg-text-primary dark:hover:text-gray-100'}"
									aria-pressed={selectedConnectionType === 'featured'}
									on:click={() => {
										selectedTag = '';
										selectedConnectionType = 'featured';
									}}
								>
									{$i18n.t('Featured')}
								</button>
							{/if}

							{#if items.find((item) => item.model?.connection_type === 'local') || items.find((item) => item.model?.connection_type === 'external') || items.find((item) => item.model?.direct) || tags.length > 0}
								<button
									class="shrink-0 h-8 px-3 rounded-full text-xs font-hg-body outline-none transition capitalize {selectedTag ===
										'' && selectedConnectionType === ''
										? 'bg-hg-text-primary dark:bg-gray-100 text-white dark:text-gray-900'
										: 'bg-hg-bg-muted dark:bg-gray-800 border border-hg-border dark:border-gray-700 text-hg-text-tertiary dark:text-gray-400 hover:text-hg-text-primary dark:hover:text-gray-100'}"
									aria-pressed={selectedTag === '' && selectedConnectionType === ''}
									on:click={() => {
										selectedConnectionType = '';
										selectedTag = '';
									}}
								>
									{$i18n.t('All')}
								</button>
							{/if}

							{#if items.find((item) => item.model?.connection_type === 'local')}
								<button
									class="shrink-0 h-8 px-3 rounded-full text-xs font-hg-body outline-none transition capitalize {selectedConnectionType ===
									'local'
										? 'bg-hg-text-primary dark:bg-gray-100 text-white dark:text-gray-900'
										: 'bg-hg-bg-muted dark:bg-gray-800 border border-hg-border dark:border-gray-700 text-hg-text-tertiary dark:text-gray-400 hover:text-hg-text-primary dark:hover:text-gray-100'}"
									aria-pressed={selectedConnectionType === 'local'}
									on:click={() => {
										selectedTag = '';
										selectedConnectionType = 'local';
									}}
								>
									{$i18n.t('Local')}
								</button>
							{/if}

							<!-- External tab hidden intentionally — all OpenAI-compatible models are "external" by default,
						     making this tab redundant noise. Re-enable by removing the `false &&` guard below. -->
							{#if false && items.find((item) => item.model?.connection_type === 'external')}
								<button
									class="shrink-0 h-8 px-3 rounded-full text-xs font-hg-body outline-none transition capitalize {selectedConnectionType ===
									'external'
										? 'bg-hg-text-primary dark:bg-gray-100 text-white dark:text-gray-900'
										: 'bg-hg-bg-muted dark:bg-gray-800 border border-hg-border dark:border-gray-700 text-hg-text-tertiary dark:text-gray-400 hover:text-hg-text-primary dark:hover:text-gray-100'}"
									aria-pressed={selectedConnectionType === 'external'}
									on:click={() => {
										selectedTag = '';
										selectedConnectionType = 'external';
									}}
								>
									{$i18n.t('External')}
								</button>
							{/if}

							{#if items.find((item) => item.model?.direct)}
								<button
									class="shrink-0 h-8 px-3 rounded-full text-xs font-hg-body outline-none transition capitalize {selectedConnectionType ===
									'direct'
										? 'bg-hg-text-primary dark:bg-gray-100 text-white dark:text-gray-900'
										: 'bg-hg-bg-muted dark:bg-gray-800 border border-hg-border dark:border-gray-700 text-hg-text-tertiary dark:text-gray-400 hover:text-hg-text-primary dark:hover:text-gray-100'}"
									aria-pressed={selectedConnectionType === 'direct'}
									on:click={() => {
										selectedTag = '';
										selectedConnectionType = 'direct';
									}}
								>
									{$i18n.t('Direct')}
								</button>
							{/if}

							{#each tags as tag}
								<Tooltip content={tag}>
									<button
										class="shrink-0 h-8 px-3 rounded-full text-xs font-hg-body outline-none transition capitalize {selectedTag ===
										tag
											? 'bg-hg-text-primary dark:bg-gray-100 text-white dark:text-gray-900'
											: 'bg-hg-bg-muted dark:bg-gray-800 border border-hg-border dark:border-gray-700 text-hg-text-tertiary dark:text-gray-400 hover:text-hg-text-primary dark:hover:text-gray-100'}"
										aria-pressed={selectedTag === tag}
										on:click={() => {
											selectedConnectionType = '';
											selectedTag = tag;
										}}
									>
										{tag.length > 16 ? `${tag.slice(0, 16)}...` : tag}
									</button>
								</Tooltip>
							{/each}
						</div>
					</div>
				{/if}

				<div class="px-3 pt-3 pb-2">
					<p class="text-sm text-hg-text-tertiary dark:text-gray-500">{$i18n.t('AI Models')}</p>
				</div>

				<div class="max-h-64 overflow-y-auto group relative">
					{#if selectedConnectionType === 'featured'}
						{#each featuredModels as entry, index (entry.model_id)}
							<ModelItem
								{selectedModelIdx}
								item={featuredToItem(entry)}
								{index}
								{value}
								{pinModelHandler}
								{unloadModelHandler}
								{deleteModelHandler}
								onClick={() => {
									value = entry.model_id;
									selectedModelIdx = index;
									show = false;
								}}
							/>
						{:else}
							<div class="block px-3 py-2 text-sm text-gray-700 dark:text-gray-100">
								{$i18n.t('No featured models available')}
							</div>
						{/each}
					{:else if filteredItems.length === 0}
						{#if items.length === 0 && $user?.role === 'admin'}
							<div class="flex flex-col items-start justify-center py-6 px-4 text-start">
								<div class="text-sm font-medium text-gray-900 dark:text-gray-100 mb-1">
									{$i18n.t('No models available')}
								</div>
								<div class="text-xs text-gray-500 dark:text-gray-400 mb-4">
									{$i18n.t('Connect to an AI provider to start chatting')}
								</div>
								<a
									href="/admin/settings/connections"
									class="px-4 py-1.5 rounded-xl text-xs font-medium bg-gray-900 dark:bg-white text-white dark:text-gray-900 hover:bg-gray-800 dark:hover:bg-gray-100 transition"
									on:click={() => {
										show = false;
									}}
								>
									{$i18n.t('Manage Connections')}
								</a>
							</div>
						{:else}
							<div class="">
								<div class="block px-3 py-2 text-sm text-gray-700 dark:text-gray-100">
									{$i18n.t('No results found')}
								</div>
							</div>
						{/if}
					{:else}
						<!-- svelte-ignore a11y-no-static-element-interactions -->
						<div
							class="max-h-64 overflow-y-auto"
							role="listbox"
							aria-label={$i18n.t('Available models')}
							bind:this={listContainer}
							on:scroll={() => {
								listScrollTop = listContainer.scrollTop;
							}}
						>
							<div style="height: {visibleStart * ITEM_HEIGHT}px;"></div>
							{#each filteredItems.slice(visibleStart, visibleEnd) as item, i (item.value)}
								{@const index = visibleStart + i}
								<ModelItem
									{selectedModelIdx}
									{item}
									{index}
									{value}
									{pinModelHandler}
									{unloadModelHandler}
									{deleteModelHandler}
									onClick={() => {
										value = item.value;
										selectedModelIdx = index;

										show = false;
									}}
								/>
							{/each}
							<div style="height: {(filteredItems.length - visibleEnd) * ITEM_HEIGHT}px;"></div>
						</div>
					{/if}

					{#if !(searchValue.trim() in $MODEL_DOWNLOAD_POOL) && searchValue && ollamaVersion && $user?.role === 'admin'}
						<Tooltip
							content={$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, {
								searchValue: searchValue
							})}
							placement="top-start"
						>
							<button
								class="flex w-full font-medium line-clamp-1 select-none items-center rounded-button py-2 pl-3 pr-1.5 text-sm text-gray-700 dark:text-gray-100 outline-hidden transition-all duration-75 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl cursor-pointer data-highlighted:bg-muted"
								on:click={() => {
									pullModelHandler();
								}}
							>
								<div class=" truncate">
									{$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, { searchValue: searchValue })}
								</div>
							</button>
						</Tooltip>
					{/if}

					{#each Object.keys($MODEL_DOWNLOAD_POOL) as model}
						<div
							class="flex w-full justify-between font-medium select-none rounded-button py-2 pl-3 pr-1.5 text-sm text-gray-700 dark:text-gray-100 outline-hidden transition-all duration-75 rounded-xl cursor-pointer data-highlighted:bg-muted"
						>
							<div class="flex">
								<div class="mr-2.5 translate-y-0.5">
									<Spinner />
								</div>

								<div class="flex flex-col self-start">
									<div class="flex gap-1">
										<div class="line-clamp-1">
											Downloading "{model}"
										</div>

										<div class="shrink-0">
											{'pullProgress' in $MODEL_DOWNLOAD_POOL[model]
												? `(${$MODEL_DOWNLOAD_POOL[model].pullProgress}%)`
												: ''}
										</div>
									</div>

									{#if 'digest' in $MODEL_DOWNLOAD_POOL[model] && $MODEL_DOWNLOAD_POOL[model].digest}
										<div class="-mt-1 h-fit text-[0.7rem] dark:text-gray-500 line-clamp-1">
											{$MODEL_DOWNLOAD_POOL[model].digest}
										</div>
									{/if}
								</div>
							</div>

							<div class="mr-2 ml-1 translate-y-0.5">
								<Tooltip content={$i18n.t('Cancel')}>
									<button
										class="text-gray-800 dark:text-gray-100"
										aria-label={$i18n.t('Cancel download of {{model}}', { model: model })}
										on:click={() => {
											cancelModelPullHandler(model);
										}}
									>
										<svg
											class="w-4 h-4 text-gray-800 dark:text-white"
											aria-hidden="true"
											xmlns="http://www.w3.org/2000/svg"
											width="24"
											height="24"
											fill="currentColor"
											viewBox="0 0 24 24"
										>
											<path
												stroke="currentColor"
												stroke-linecap="round"
												stroke-linejoin="round"
												stroke-width="2"
												d="M6 18 17.94 6M18 18 6.06 6"
											/>
										</svg>
									</button>
								</Tooltip>
							</div>
						</div>
					{/each}
				</div>

				<div class="border-t border-hg-border-subtle dark:border-gray-800 p-3">
					<button
						class="flex items-start gap-2 w-full text-left"
						on:click={toggleDefaultModel}
						aria-pressed={isCurrentModelDefault}
					>
						<div
							class="mt-0.5 size-4 rounded-[4px] border border-hg-text-tertiary dark:border-gray-500 flex items-center justify-center shrink-0"
						>
							{#if isCurrentModelDefault}
								<svg
									viewBox="0 0 12 12"
									class="size-3 text-hg-text-primary dark:text-gray-100"
									fill="none"
								>
									<path
										d="M2 6l3 3 5-5"
										stroke="currentColor"
										stroke-width="1.5"
										stroke-linecap="round"
										stroke-linejoin="round"
									/>
								</svg>
							{/if}
						</div>
						<span
							class="text-sm font-semibold font-hg-body text-hg-text-primary dark:text-gray-100"
						>
							{$i18n.t('Set as default model')}
						</span>
					</button>
				</div>
			</slot>
		</DropdownMenu.Content>
	</DropdownMenu.Portal>
</DropdownMenu.Root>

<style>
	/* Open/close motion. bits-ui 0.21 drove this through a `transition` prop; 2.x
		removed it and instead marks the content with data-starting-style (first frame
		open) and data-ending-style (while closing), holding the unmount until the
		animation finishes. Values mirror the flyAndScale this menu used before:
		y -8px, scale 0.95, 200ms cubicOut.

		:global because the menu is portalled out of this component's subtree and the
		class lands on bits-ui's own content element — a scoped selector would be
		compiled away as unused and never match. */
	:global(.model-selector-panel) {
		opacity: 1;
		transform: translateY(0) scale(1);
		transform-origin: var(--bits-dropdown-menu-content-transform-origin, center);
		transition:
			opacity 200ms cubic-bezier(0.33, 1, 0.68, 1),
			transform 200ms cubic-bezier(0.33, 1, 0.68, 1);
	}

	:global(.model-selector-panel[data-starting-style]),
	:global(.model-selector-panel[data-ending-style]) {
		opacity: 0;
		transform: translateY(-8px) scale(0.95);
	}

	@media (prefers-reduced-motion: reduce) {
		:global(.model-selector-panel) {
			transition: none;
		}

		/* Land on the final values straight away instead of flashing the offset
			start/end frame with the transition switched off. */
		:global(.model-selector-panel[data-starting-style]),
		:global(.model-selector-panel[data-ending-style]) {
			opacity: 1;
			transform: none;
		}
	}
</style>
=======
	{#if show}
		<div
			use:portal
			bind:this={contentElement}
			style="position: fixed; z-index: 9999; top: {dropdownPosition.top}px; left: {dropdownPosition.left}px;"
		>
			<div
				bind:this={panelElement}
				class="z-40 {className ??
					'w-[20rem]'} max-w-[calc(100vw-1rem)] justify-start rounded-xl border border-gray-100 bg-white p-0.5 shadow-lg outline-hidden dark:border-gray-800 dark:bg-gray-850 dark:text-white flex flex-col overflow-hidden"
				style={dropdownPosition.maxHeight ? `max-height: ${dropdownPosition.maxHeight}px;` : ''}
				transition:flyAndScale
			>
				<slot>
					{#if searchEnabled}
						<div class="my-0.5 flex ml-2 mr-0.5 h-[1.6875rem] shrink-0 items-center gap-2">
							<Search className=" size-3.5 shrink-0" strokeWidth="2" />

							<input
								id="model-search-input"
								bind:value={searchValue}
								class="w-full bg-transparent text-[13px] font-normal outline-hidden placeholder:text-gray-400 dark:placeholder:text-gray-500"
								placeholder={searchPlaceholder}
								autocomplete="off"
								aria-label={$i18n.t('Search In Models')}
								on:keydown={(e) => {
									if (e.code === 'Enter' && filteredItems.length > 0) {
										selectItem(filteredItems[selectedModelIdx], selectedModelIdx);
										return; // dont need to scroll on selection
									} else if (e.code === 'ArrowDown') {
										e.stopPropagation();
										selectedModelIdx = Math.min(selectedModelIdx + 1, filteredItems.length - 1);
									} else if (e.code === 'ArrowUp') {
										e.stopPropagation();
										selectedModelIdx = Math.max(selectedModelIdx - 1, 0);
									} else {
										// if the user types something, reset to the top selection.
										selectedModelIdx = 0;
									}

									const item = document.querySelector(`[data-arrow-selected="true"]`);
									item?.scrollIntoView({
										block: 'center',
										inline: 'nearest',
										behavior: 'instant'
									});
								}}
							/>

							{#if modelFilterItems.length > 0 || (multipleEnabled && items.length > 0)}
								<div class="flex min-w-0 shrink-0 items-center gap-0.5">
									{#if multipleEnabled && items.length > 0}
										<Tooltip content={$i18n.t('Compare')}>
											<button
												type="button"
												class="flex size-[1.375rem] shrink-0 items-center justify-center rounded-lg transition-colors duration-100 {compareEnabled
													? 'bg-gray-50 text-gray-700 hover:bg-gray-50 dark:bg-gray-800/60 dark:text-gray-200 dark:hover:bg-gray-800/60'
													: 'text-gray-500 hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200'}"
												aria-label={$i18n.t('Compare')}
												aria-pressed={compareEnabled}
												on:click={() => {
													setCompareEnabled(!compareEnabled);
												}}
											>
												<Keyframes className="size-3" strokeWidth="2" />
											</button>
										</Tooltip>
									{/if}

									{#if modelFilterItems.length > 0}
										<TagSelector
											bind:value={selectedFilter}
											placeholder={$i18n.t('All')}
											align="end"
											items={modelFilterItems}
											triggerClass="relative flex h-[1.375rem] max-w-32 items-center gap-0.5 rounded-xl bg-transparent px-1.5 text-[11px] font-normal text-gray-400 transition-colors duration-100 hover:bg-gray-50/40 hover:text-gray-600 dark:text-gray-500 dark:hover:bg-gray-800/40 dark:hover:text-gray-300"
											itemClass="flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-[13px] capitalize hover:bg-gray-50/40 hover:text-gray-900 dark:hover:bg-gray-800/40 dark:hover:text-gray-100"
											contentClass="min-w-36 model-selector-child-menu"
											onChange={setModelFilter}
										/>
									{/if}
								</div>
							{/if}
						</div>
					{/if}

					<div class="group relative flex min-h-0 flex-1 flex-col">
						{#if filteredItems.length === 0}
							{#if items.length === 0 && $user?.role === 'admin'}
								<div
									class="my-2 flex w-full flex-col items-start justify-center px-4 py-3 text-start"
								>
									<div
										class="mb-0.5 text-xs font-normal leading-4 text-gray-800 dark:text-gray-100"
									>
										{$i18n.t('No models available')}
									</div>
									<div class="w-full text-[11px] leading-3.5 text-gray-500 dark:text-gray-400">
										{$i18n.t('Connect to an AI provider to start chatting')}
									</div>
									<button
										type="button"
										class="mt-3 rounded-lg px-0 py-1 text-[11px] font-normal leading-none text-gray-600 underline-offset-2 transition-colors duration-100 hover:text-gray-800 hover:underline focus:outline-hidden focus:underline dark:text-gray-300 dark:hover:text-gray-100"
										on:click={() => {
											show = false;
											showSettings.set('admin:connections');
										}}
									>
										{$i18n.t('Manage Connections')}
									</button>
								</div>
							{:else}
								<div class="">
									<div class="block px-2 py-1 text-[13px] text-gray-700 dark:text-gray-100">
										{$i18n.t('No results found')}
									</div>
								</div>
							{/if}
						{:else}
							<!-- svelte-ignore a11y-no-static-element-interactions -->
							<div
								class="min-h-0 flex-1 overflow-y-auto"
								style="max-height: 288px;"
								role="listbox"
								aria-label={$i18n.t('Available models')}
								bind:this={listContainer}
								use:trackListViewport
								on:scroll={() => {
									listScrollTop = listContainer.scrollTop;
								}}
							>
								<div style="height: {visibleStart * ITEM_HEIGHT}px;" />
								{#each filteredItems.slice(visibleStart, visibleEnd) as item, i (item.value)}
									{@const index = visibleStart + i}
									<ModelItem
										{selectedModelIdx}
										{item}
										{index}
										value={primaryValue}
										{pinModelHandler}
										{unloadModelHandler}
										{deleteModelHandler}
										{selectionOnly}
										{compareEnabled}
										{selectedValues}
										onClick={() => {
											selectItem(item, index);
										}}
									/>
								{/each}
								<div style="height: {(filteredItems.length - visibleEnd) * ITEM_HEIGHT}px;" />
							</div>
						{/if}

						{#if !selectionOnly && !(searchValue.trim() in $MODEL_DOWNLOAD_POOL) && searchValue && ollamaVersion && $user?.role === 'admin'}
							<Tooltip
								content={$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, {
									searchValue: searchValue
								})}
								placement="top-start"
							>
								<button
									class="flex h-[1.6875rem] w-full cursor-pointer select-none items-center rounded-xl px-2 text-[13px] font-normal text-gray-700 outline-hidden transition-colors duration-75 hover:bg-gray-50/40 dark:text-gray-100 dark:hover:bg-gray-800/40"
									on:click={() => {
										pullModelHandler();
									}}
								>
									<div class=" truncate">
										{$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, {
											searchValue: searchValue
										})}
									</div>
								</button>
							</Tooltip>
						{/if}

						{#each selectionOnly ? [] : Object.keys($MODEL_DOWNLOAD_POOL) as model}
							<div
								class="flex min-h-[1.6875rem] w-full cursor-pointer select-none justify-between rounded-xl px-2 text-[13px] font-normal text-gray-700 outline-hidden transition-colors duration-75 dark:text-gray-100"
							>
								<div class="flex">
									<div class="mr-2.5 translate-y-0.5">
										<Spinner />
									</div>

									<div class="flex flex-col self-start">
										<div class="flex gap-1">
											<div class="line-clamp-1">
												Downloading "{model}"
											</div>

											<div class="shrink-0">
												{'pullProgress' in $MODEL_DOWNLOAD_POOL[model]
													? `(${$MODEL_DOWNLOAD_POOL[model].pullProgress}%)`
													: ''}
											</div>
										</div>

										{#if 'digest' in $MODEL_DOWNLOAD_POOL[model] && $MODEL_DOWNLOAD_POOL[model].digest}
											<div class="-mt-1 h-fit text-[0.7rem] dark:text-gray-500 line-clamp-1">
												{$MODEL_DOWNLOAD_POOL[model].digest}
											</div>
										{/if}
									</div>
								</div>

								<div class="mr-2 ml-1 translate-y-0.5">
									<Tooltip content={$i18n.t('Cancel')}>
										<button
											class="text-gray-800 dark:text-gray-100"
											aria-label={$i18n.t('Cancel download of {{model}}', { model: model })}
											on:click={() => {
												cancelModelPullHandler(model);
											}}
										>
											<svg
												class="w-4 h-4 text-gray-800 dark:text-white"
												aria-hidden="true"
												xmlns="http://www.w3.org/2000/svg"
												width="24"
												height="24"
												fill="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													d="M6 18 17.94 6M18 18 6.06 6"
												/>
											</svg>
										</button>
									</Tooltip>
								</div>
							</div>
						{/each}
					</div>

					{#if showSetDefault}
						<div class="flex shrink-0 items-center justify-end px-2 py-1 leading-none">
							<button
								type="button"
								class="text-[0.65rem] font-normal leading-none text-gray-500 underline-offset-2 transition-colors duration-100 hover:text-gray-700 hover:underline dark:text-gray-500 dark:hover:text-gray-300"
								on:click|stopPropagation={setDefaultHandler}
							>
								{$i18n.t('Set as default')}
							</button>
						</div>
					{:else}
						<div class="shrink-0 pb-1"></div>
					{/if}

					<div class="hidden w-[42rem]" />
					<div class="hidden w-[28rem]" />
					<div class="hidden w-[24rem]" />
					<div class="hidden w-[22rem]" />
					<div class="hidden w-[20rem]" />
				</slot>
			</div>
		</div>
	{/if}
</div>
>>>>>>> v0.11.0
