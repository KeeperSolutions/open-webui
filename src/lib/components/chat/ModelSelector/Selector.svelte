<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import Fuse from 'fuse.js';

	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import Spinner from '$lib/components/common/Spinner.svelte';
	import { flyAndScale } from '$lib/utils/transitions';

	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';

	import { deleteModel, getOllamaVersion, pullModel, unloadModel } from '$lib/apis/ollama';

	import { user, MODEL_DOWNLOAD_POOL, models, mobile, settings, config, theme } from '$lib/stores';
	import type { Model } from '$lib/stores';
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

	import ModelItem from './ModelItem.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let id = '';
	export let value = '';
	export let placeholder = $i18n.t('Select a model');
	export let searchEnabled = true;
	export let searchPlaceholder = $i18n.t('Search a model');

	export let items: {
		label: string;
		value: string;
		model: Model;
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		[key: string]: any;
	}[] = [];

	export let triggerClassName = 'text-lg';

	export let pinModelHandler: (modelId: string) => void = () => {};

	let tagsContainerElement;

	let show = false;
	let tags = [];

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

	let searchValue = '';

	let selectedTag = '';
	let selectedConnectionType = '';

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
	).filter((item) => !(item.model?.info?.meta?.hidden ?? false));

	$: if (searchValue && selectedConnectionType === 'featured') {
		selectedConnectionType = '';
	}

	$: if (selectedTag || selectedConnectionType) {
		resetView();
	}

	const resetView = async () => {
		await tick();

		const isFeatured = selectedConnectionType === 'featured';
		const activeList = isFeatured ? featuredModels : filteredItems;
		const selectedInActive = isFeatured
			? activeList.findIndex((entry) => entry.model_id === value)
			: activeList.findIndex((item) => item.value === value);

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

	onMount(async () => {
		if (items) {
			tags = items
				.filter((item) => !(item.model?.info?.meta?.hidden ?? false))
				.flatMap((item) => item.model?.tags ?? [])
				.map((tag) => tag.name.toLowerCase());
			// Remove duplicates and sort
			tags = Array.from(new Set(tags)).sort((a, b) => a.localeCompare(b));
		}

		try {
			const config = await getFeaturedModels(localStorage.token);
			const raw = config?.FEATURED_MODELS;
			if (Array.isArray(raw) && raw.length > 0) {
				rawFeaturedModels = raw;
			}
		} catch {
			// non-blocking — featured models are best-effort
		}
	});

	$: if (show) {
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

	const ITEM_HEIGHT = 42;
	const OVERSCAN = 10;

	let listScrollTop = 0;
	let listContainer;

	$: visibleStart = Math.max(0, Math.floor(listScrollTop / ITEM_HEIGHT) - OVERSCAN);
	$: visibleEnd = Math.min(
		filteredItems.length,
		Math.ceil((listScrollTop + 256) / ITEM_HEIGHT) + OVERSCAN
	);
</script>

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
		class="relative w-full {($settings?.highContrastMode ?? false)
			? ''
			: 'outline-hidden focus:outline-hidden'}"
		aria-label={selectedModel
			? $i18n.t('Selected model: {{modelName}}', { modelName: selectedModel.label })
			: placeholder}
		id="model-selector-{id}-button"
	>
		<div
			class="flex w-full items-center justify-between gap-2 px-3 py-2 rounded-hg-md border border-hg-border-subtle dark:border-gray-800 bg-transparent text-left truncate {triggerClassName}"
			role="presentation"
			on:mouseenter={async () => {
				models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
			}}
		>
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
		</div>
	</DropdownMenu.Trigger>

	<DropdownMenu.Content
		class="z-40 {$mobile
			? 'w-full'
			: 'w-[400px]'} max-w-[calc(100vw-1rem)] rounded-2xl bg-white dark:bg-gray-850 dark:text-white border border-hg-border-subtle dark:border-gray-800 shadow-lg outline-hidden overflow-hidden"
		transition={flyAndScale}
		side={$mobile ? 'bottom' : 'bottom-start'}
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
									aria-label={$i18n.t('Cancel')}
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
					<span class="text-sm font-semibold font-hg-body text-hg-text-primary dark:text-gray-100">
						{$i18n.t('Set as default model')}
					</span>
				</button>
			</div>
		</slot>
	</DropdownMenu.Content>
</DropdownMenu.Root>
