<script lang="ts">
	import { marked } from 'marked';
	import Fuse from 'fuse.js';

	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import Spinner from '$lib/components/common/Spinner.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import { flyAndScale } from '$lib/utils/transitions';

	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';

	import { deleteModel, getOllamaVersion, pullModel } from '$lib/apis/ollama';
	import { deleteModelById } from '$lib/apis/models';
	import { unloadModel } from '$lib/apis';
	import { getFeaturedModels } from '$lib/apis/configs';
	import { updateUserSettings } from '$lib/apis/users';

	import {
		user,
		MODEL_DOWNLOAD_POOL,
		models,
		temporaryChatEnabled,
		settings,
		config,
		showSettings,
		mobile,
		theme
	} from '$lib/stores';
	import { toast } from 'svelte-sonner';
	import { capitalizeFirstLetter, sanitizeResponseContent, splitStream } from '$lib/utils';
	import { resolveTheme } from '$lib/utils/theme';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { getModels } from '$lib/apis';

	import HgIconChevronRight from '$lib/components/icons/HgIconChevronRight.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';
	import Keyframes from '$lib/components/icons/Keyframes.svelte';

	import ModelItem from './ModelItem.svelte';
	import { buildFeaturedModels, featuredToItem } from './featuredModels';
	import { isDefaultModel as isDefaultModelId, toggleDefaultModel } from './defaultModel';

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

	export let className = 'w-[400px]';
	export let triggerClassName = 'text-lg';
	export let placement: 'top' | 'bottom' | 'auto' = 'bottom';
	export let align: 'start' | 'end' = 'start';

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
			// Re-fetch on every open because fetch goes stale the moment an
			// admin edits the featured list elsewhere (e.g. the Featured Models
			// modal, which saves through its own endpoint and has no way to notify
			// this component).
			loadFeaturedModels();
			// Open on the Featured pill whenever there are featured models to show,
			// regardless of whether the current/default model is one of them. The
			// reactive block above re-applies this if the fetch resolves later.
			connectionTypeTouched = false;
			selectedConnectionType = featuredModels.length > 0 ? 'featured' : '';
			resetView();
			updatePosition();
			await tick();
			updatePosition();
			if (!$mobile) {
				window.setTimeout(() => document.getElementById('model-search-input')?.focus(), 0);
			}
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

	// Split the selected model's label at its first space so the trigger can render
	// "Head – tail" (e.g. "GPT-5.2 – chat-latest"). Compare mode keeps the flat label.
	$: showSplitLabel = !!selectedModel && !(compareEnabled && selectedCount > 1);
	$: selectedNameHead = (() => {
		if (!showSplitLabel) return '';
		const label = (selectedModel.label ?? '').trim();
		const spaceIdx = label.indexOf(' ');
		return spaceIdx === -1 ? label : label.slice(0, spaceIdx);
	})();
	$: selectedNameTail = (() => {
		if (!showSplitLabel) return '';
		const label = (selectedModel.label ?? '').trim();
		const spaceIdx = label.indexOf(' ');
		return spaceIdx === -1 ? '' : label.slice(spaceIdx + 1).trim();
	})();

	$: resolvedTheme = resolveTheme($theme);

	let searchValue = '';

	let selectedTag = '';
	let selectedConnectionType = '';
	// Set once the user picks a pill by hand while the dropdown is open, so a
	// late-arriving featured-models fetch doesn't yank them off their choice.
	let connectionTypeTouched = false;

	// Featured Models (TRAU-469): admin-curated list, shown first under a "Featured" pill.
	let featuredModelsConfig: import('./featuredModels').FeaturedModelConfig[] = [];
	$: featuredModels = buildFeaturedModels(featuredModelsConfig, items, includeHidden);

	// Featured models load async (see loadFeaturedModels), often after the dropdown
	// has already opened. Once they arrive, snap to the Featured pill — unless the
	// user has already chosen a different pill this time it was opened.
	$: if (show && !connectionTypeTouched && featuredModels.length > 0 && !searchValue) {
		selectedConnectionType = 'featured';
	}

	const loadFeaturedModels = async () => {
		if (!localStorage.token) return;
		try {
			const config = await getFeaturedModels(localStorage.token);
			const entries = config?.FEATURED_MODELS;
			// Assign on any array response, including []: an admin removing the
			// last featured entry is a valid, real update, not a fetch failure —
			// the old `entries.length > 0` guard here left a removed-to-empty
			// list stuck showing its last non-empty snapshot until page reload.
			if (Array.isArray(entries)) {
				featuredModelsConfig = entries;
			}
		} catch {
			// non-blocking — featured models are best-effort
		}
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
							// The Featured pill renders the curated `featuredModels` list in its
							// own template block — the normal list must be empty in that mode.
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
							// The Featured pill renders the curated `featuredModels` list in its
							// own template block — the normal list must be empty in that mode.
							return false;
						}
					})
	).filter((item) => includeHidden || !(item.model?.info?.meta?.hidden ?? false));

	// Typing a search leaves the Featured pill (search results come from filteredItems).
	$: if (searchValue && selectedConnectionType === 'featured') {
		selectedConnectionType = '';
	}

	$: if (
		selectedTag !== undefined ||
		selectedConnectionType !== undefined ||
		searchValue !== undefined
	) {
		resetView();
	}

	const resetView = async () => {
		await tick();

		const onFeatured = selectedConnectionType === 'featured';
		const selectedInList = onFeatured
			? featuredModels.findIndex((entry) => entry.model_id === primaryValue)
			: filteredItems.findIndex((item) => item.value === primaryValue);

		if (selectedInList >= 0) {
			// The selected model is visible in the current filter
			selectedModelIdx = selectedInList;
		} else {
			// The selected model is not visible, default to first item in the list
			selectedModelIdx = 0;
		}

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

	$: isDefaultModel = (modelId: string) => isDefaultModelId($settings, modelId);

	// Per-model "Set as default" from the row menu. Toggling off clears the saved
	// default entirely — the next new chat then falls back to the admin server
	// default (DEFAULT_MODELS), or the first available model if none is configured.
	const setDefaultHandler = async (modelId: string) => {
		const result = toggleDefaultModel($settings, modelId);
		if (!result) return;
		// Persist first — updateUserSettings throws on failure (network error or
		// a non-ok response). Only update the local store and report success
		// once the save is actually confirmed; otherwise the UI would mark this
		// model as the default (used for every new chat) even though it was
		// never saved, silently reverting on the next reload.
		try {
			await updateUserSettings(localStorage.token, { ui: result.nextSettings });
			settings.set(result.nextSettings);
			toast.success(
				result.cleared ? $i18n.t('Default model unset') : $i18n.t('Default model updated')
			);
		} catch (error) {
			toast.error($i18n.t('Failed to update settings'));
		}
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

		loadFeaturedModels();

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

	const ITEM_HEIGHT = 32;
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

<svelte:window
	on:pointerdown={handlePointerDown}
	on:keydown={handleKeydown}
	on:resize={scheduleSettledPositionUpdates}
/>

<div class="relative w-full">
	<button
		bind:this={triggerElement}
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
		{disabled}
		on:click={toggleOpen}
	>
		<div
			class="flex w-full min-w-0 items-center justify-between gap-2 text-left px-0.5 bg-transparent {triggerClassName} {($settings?.highContrastMode ??
			false)
				? 'dark:placeholder-gray-100 placeholder-gray-800'
				: 'placeholder-gray-400'}"
			on:mouseenter={async () => {
				models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
			}}
		>
			<div class="flex min-w-0 items-center gap-2">
				{#if selectedModel}
					<img
						src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${encodeURIComponent(
							selectedModel.value
						)}&theme=${resolvedTheme}&lang=${$i18n.language}`}
						alt=""
						class="size-5 rounded-full shrink-0"
						loading="lazy"
					/>
					{#if showSplitLabel}
						<span class="truncate">
							<span class="text-hg-text-primary dark:text-gray-100">{selectedNameHead}</span
							>{#if selectedNameTail}<span class="text-hg-text-tertiary dark:text-gray-500">
									{' '}– {selectedNameTail}</span
								>{/if}
						</span>
					{:else}
						<span class="min-w-0 truncate">{triggerLabel}</span>
					{/if}
				{:else}
					<span class="text-hg-text-tertiary dark:text-gray-400 truncate">{placeholder}</span>
				{/if}
			</div>
			<HgIconChevronRight class="shrink-0 size-5 text-hg-orange dark:text-gray-400 rotate-90" />
		</div>
	</button>

	{#if show}
		<div
			use:portal
			bind:this={contentElement}
			style="position: fixed; z-index: 9999; top: {dropdownPosition.top}px; left: {dropdownPosition.left}px;"
		>
			<div
				bind:this={panelElement}
				class="z-40 {className ??
					'w-[400px]'} max-w-[calc(100vw-1rem)] justify-start rounded-2xl border border-hg-border-subtle bg-white shadow-lg outline-hidden dark:border-gray-800 dark:bg-gray-850 dark:text-white flex flex-col overflow-hidden"
				style={dropdownPosition.maxHeight ? `max-height: ${dropdownPosition.maxHeight}px;` : ''}
				transition:flyAndScale
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
									class="flex-1 min-w-0 text-sm bg-transparent outline-hidden text-hg-text-primary dark:text-gray-100 placeholder:text-hg-text-tertiary dark:placeholder:text-gray-500"
									placeholder={searchPlaceholder}
									autocomplete="off"
									aria-label={$i18n.t('Search In Models')}
									on:keydown={(e) => {
										const onFeatured = selectedConnectionType === 'featured';
										const activeList = onFeatured ? featuredModels : filteredItems;
										if (e.code === 'Enter' && activeList.length > 0) {
											const chosen = onFeatured
												? featuredToItem(featuredModels[selectedModelIdx], items)
												: filteredItems[selectedModelIdx];
											selectItem(chosen, selectedModelIdx);
											return; // dont need to scroll on selection
										} else if (e.code === 'ArrowDown') {
											e.stopPropagation();
											selectedModelIdx = Math.min(selectedModelIdx + 1, activeList.length - 1);
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

								{#if multipleEnabled && items.length > 0}
									<Tooltip content={$i18n.t('Compare')}>
										<button
											type="button"
											class="flex size-[1.375rem] shrink-0 items-center justify-center rounded-lg transition-colors duration-100 {compareEnabled
												? 'bg-gray-100 text-gray-700 dark:bg-gray-800/60 dark:text-gray-200'
												: 'text-hg-text-tertiary hover:text-hg-text-primary dark:text-gray-400 dark:hover:text-gray-200'}"
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
							</div>
						</div>
					{/if}

					{#if items.filter((item) => includeHidden || !(item.model?.info?.meta?.hidden ?? false)).length > 0}
						<div
							class="flex gap-1 p-3 border-b border-hg-border-subtle dark:border-gray-800 overflow-x-auto scrollbar-none"
							on:wheel={(e) => {
								if (e.deltaY !== 0) {
									e.preventDefault();
									e.currentTarget.scrollLeft += e.deltaY;
								}
							}}
						>
							<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
							<div
								class="flex gap-1 w-fit whitespace-nowrap"
								on:click={() => {
									connectionTypeTouched = true;
								}}
							>
								{#if featuredModels.length > 0 && !searchValue}
									<button
										type="button"
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

								{#if items.find((item) => item.model?.connection_type === 'local') || items.find((item) => item.model?.direct) || tags.length > 0 || featuredModels.length > 0}
									<button
										type="button"
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
										type="button"
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

								<!--
									External pill hidden intentionally — every OpenAI-compatible model is
									"external" by default, so the pill is redundant noise. Kept for reference;
									restore by uncommenting and adding `external` back to the filter switch.

									{#if items.find((item) => item.model?.connection_type === 'external')}
										<button
											type="button"
											class="shrink-0 h-8 px-3 rounded-full text-xs font-hg-body outline-none transition capitalize {selectedConnectionType === 'external'
												? 'bg-hg-text-primary dark:bg-gray-100 text-white dark:text-gray-900'
												: 'bg-hg-bg-muted dark:bg-gray-800 border border-hg-border dark:border-gray-700 text-hg-text-tertiary dark:text-gray-400 hover:text-hg-text-primary dark:hover:text-gray-100'}"
											aria-pressed={selectedConnectionType === 'external'}
											on:click={() => { selectedTag = ''; selectedConnectionType = 'external'; }}
										>
											{$i18n.t('External')}
										</button>
									{/if}
								-->

								{#if items.find((item) => item.model?.direct)}
									<button
										type="button"
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
											type="button"
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

					<div class="group relative flex min-h-0 flex-1 flex-col">
						{#if selectedConnectionType === 'featured'}
							<div
								class="min-h-0 flex-1 overflow-y-auto"
								style="max-height: 288px;"
								role="listbox"
								aria-label={$i18n.t('Featured models')}
							>
								{#each featuredModels as entry, index (entry.model_id)}
									<ModelItem
										featured
										{selectedModelIdx}
										item={featuredToItem(entry, items)}
										{index}
										value={primaryValue}
										{pinModelHandler}
										{unloadModelHandler}
										{deleteModelHandler}
										{selectionOnly}
										{compareEnabled}
										{selectedValues}
										{setDefaultHandler}
										isDefault={isDefaultModel(entry.model_id)}
										onClick={() => {
											selectItem(featuredToItem(entry, items), index);
										}}
									/>
								{:else}
									<div class="block px-3 py-2 text-[13px] text-gray-700 dark:text-gray-100">
										{$i18n.t('No featured models available')}
									</div>
								{/each}
							</div>
						{:else if filteredItems.length === 0}
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
										{setDefaultHandler}
										isDefault={isDefaultModel(item.value)}
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

					<div class="shrink-0 pb-1"></div>

					<!-- Tailwind JIT hints: `className` is a dynamic prop, keep the widths it may take. -->
					<div class="hidden w-[400px]"></div>
					<div class="hidden w-[42rem]"></div>
					<div class="hidden w-[28rem]"></div>
					<div class="hidden w-[24rem]"></div>
					<div class="hidden w-[22rem]"></div>
					<div class="hidden w-[20rem]"></div>
				</slot>
			</div>
		</div>
	{/if}
</div>
