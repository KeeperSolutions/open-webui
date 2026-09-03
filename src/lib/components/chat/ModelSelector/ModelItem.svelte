<script lang="ts">
	import { marked } from 'marked';
	import dayjs from '$lib/dayjs';

	import { getContext } from 'svelte';

	import { user, theme } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { copyToClipboard, sanitizeResponseContent } from '$lib/utils';
	import { resolveTheme } from '$lib/utils/theme';
	import ArrowUpTray from '$lib/components/icons/ArrowUpTray.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import ModelItemMenu from './ModelItemMenu.svelte';
	import EllipsisVertical from '$lib/components/icons/EllipsisVertical.svelte';
	import Tag from '$lib/components/icons/Tag.svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	export let selectedModelIdx: number = -1;
	export let item: any = {};
	export let index: number = -1;
	export let value: string = '';

	export let unloadModelHandler: (modelValue: string) => void = () => {};
	export let pinModelHandler: (modelId: string) => void = () => {};
	export let deleteModelHandler: (model: any) => void = () => {};

	export let onClick: () => void = () => {};

	const copyLinkHandler = async (model) => {
		const baseUrl = window.location.origin;
		const res = await copyToClipboard(`${baseUrl}/?model=${encodeURIComponent(model.id)}`);

		if (res) {
			toast.success($i18n.t('Copied link to clipboard'));
		} else {
			toast.error($i18n.t('Failed to copy link'));
		}
	};

	let showMenu = false;

	// Resolve theme to 'light' or 'dark' for API call
	$: resolvedTheme = resolveTheme($theme);

	// providerName prop is only set by featured entries (curated brand name e.g. "Google").
	// For All/Local tabs owned_by is infrastructure ("ollama"), not the model brand — ignore it.
	$: providerDisplay = (item?.providerName as string) ?? '';

	// Line 1 two-tone split: provider name in primary, model name in tertiary.
	// With explicit provider: nameHead = provider, nameTail = full label.
	// Without provider: split label on first space as before.
	$: nameHead = (() => {
		if (providerDisplay) return providerDisplay;
		const label = (item?.label ?? '').trim();
		const spaceIdx = label.indexOf(' ');
		return spaceIdx === -1 ? label : label.slice(0, spaceIdx);
	})();
	$: nameTail = (() => {
		const label = (item?.label ?? '').trim();
		if (providerDisplay) return label;
		const spaceIdx = label.indexOf(' ');
		return spaceIdx === -1 ? '' : label.slice(spaceIdx + 1).trim();
	})();

	// Line 2 capability string: model tags joined, else fall back to description.
	$: capabilityLine = (() => {
		const tags = (item?.model?.tags ?? []).map((t: { name?: string }) => t?.name).filter(Boolean);
		if (tags.length > 0) {
			return tags.join(' • ');
		}
		return (item?.model?.info?.meta?.description ?? '').trim();
	})();
</script>

<button
	aria-roledescription="model-item"
	role="option"
	aria-selected={value === item.value}
	aria-label={$i18n.t('Select {{modelName}} model', { modelName: item.label })}
	class="flex group/item w-full text-left select-none items-center rounded-xl px-3 py-2 outline-hidden transition-all duration-75 hover:bg-hg-bg-muted dark:hover:bg-gray-800 cursor-pointer data-highlighted:bg-muted {index ===
	selectedModelIdx
		? 'bg-hg-bg-muted dark:bg-gray-800 group-hover:bg-transparent'
		: ''}"
	data-arrow-selected={index === selectedModelIdx}
	data-value={item.value}
	on:click={() => {
		onClick();
	}}
>
	<div class="flex flex-[1_0_0] items-center gap-2 min-w-px">
		<Tooltip content={$user?.role === 'admin' ? (item?.value ?? '') : ''} placement="top-start">
			<img
				src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${item.model.id}&theme=${resolvedTheme}&lang=${$i18n.language}`}
				alt={$i18n.t('{{modelName}} profile image', { modelName: item.label })}
				class="rounded-full size-5 shrink-0"
				loading="lazy"
				on:error={(e) => {
					e.currentTarget.src = '/favicon.png';
				}}
			/>
		</Tooltip>
		<div class="flex flex-[1_0_0] flex-col gap-1 items-start min-w-px">
			<span class="font-semibold text-base truncate w-full leading-[1.4]">
				<span class="text-hg-text-primary dark:text-gray-100">{nameHead}</span>{#if nameTail}<span
						class="text-hg-text-tertiary dark:text-gray-500">{' '}– {nameTail}</span
					>{/if}
			</span>
			{#if capabilityLine}
				<p class="text-xs text-hg-text-tertiary dark:text-gray-500 truncate w-full leading-[1.4]">
					{capabilityLine}
				</p>
			{/if}

			<div class="shrink-0 flex items-center gap-2">
				{#if item.model.owned_by === 'ollama'}
					{#if (item.model.ollama?.details?.parameter_size ?? '') !== ''}
						<div class="flex items-center translate-y-[0.5px]">
							<Tooltip
								content={`${
									item.model.ollama?.details?.quantization_level
										? item.model.ollama?.details?.quantization_level + ' '
										: ''
								}${
									item.model.ollama?.size
										? `(${(item.model.ollama?.size / 1024 ** 3).toFixed(1)}GB)`
										: ''
								}`}
								className="self-end"
							>
								<span class=" text-xs font-medium text-gray-600 dark:text-gray-400 line-clamp-1"
									>{item.model.ollama?.details?.parameter_size ?? ''}</span
								>
							</Tooltip>
						</div>
					{/if}
				{/if}

				{#if item.model.loaded}
					<div class="flex items-center translate-y-[0.5px] px-0.5">
						<Tooltip
							content={item.model.ollama?.expires_at &&
							new Date(item.model.ollama?.expires_at * 1000) > new Date()
								? `${$i18n.t('Unloads {{FROM_NOW}}', {
										FROM_NOW: dayjs(item.model.ollama?.expires_at * 1000).fromNow()
									})}`
								: `${$i18n.t('Loaded')}`}
							className="self-end"
						>
							<div class=" flex items-center">
								<span class="relative flex size-2">
									<span
										class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"
									/>
									<span class="relative inline-flex rounded-full size-2 bg-green-500" />
								</span>
							</div>
						</Tooltip>
					</div>
				{/if}

				<!-- {JSON.stringify(item.info)} -->

				{#if (item?.model?.tags ?? []).length > 0}
					{#key item.model.id}
						<Tooltip elementId="tags-{item.model.id}">
							<div slot="tooltip" id="tags-{item.model.id}">
								{#each item.model?.tags.sort((a, b) => a.name.localeCompare(b.name)) as tag}
									<Tooltip content={tag.name} className="flex-shrink-0">
										<div class=" text-xs font-medium rounded-sm uppercase text-white">
											{tag.name}
										</div>
									</Tooltip>
								{/each}
							</div>

							<div class="translate-y-[1px]">
								<Tag />
							</div>
						</Tooltip>
					{/key}
				{/if}

				{#if item.model?.direct}
					<Tooltip content={`${$i18n.t('Direct')}`}>
						<div class="translate-y-[1px]">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 16 16"
								fill="currentColor"
								class="size-3"
							>
								<path
									fill-rule="evenodd"
									d="M2 2.75A.75.75 0 0 1 2.75 2C8.963 2 14 7.037 14 13.25a.75.75 0 0 1-1.5 0c0-5.385-4.365-9.75-9.75-9.75A.75.75 0 0 1 2 2.75Zm0 4.5a.75.75 0 0 1 .75-.75 6.75 6.75 0 0 1 6.75 6.75.75.75 0 0 1-1.5 0C8 10.35 5.65 8 2.75 8A.75.75 0 0 1 2 7.25ZM3.5 11a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z"
									clip-rule="evenodd"
								/>
							</svg>
						</div>
					</Tooltip>
				{:else if item.model.connection_type === 'external'}
					<Tooltip content={`${$i18n.t('External')}`}>
						<div class="translate-y-[1px]">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 16 16"
								fill="currentColor"
								class="size-3"
							>
								<path
									fill-rule="evenodd"
									d="M8.914 6.025a.75.75 0 0 1 1.06 0 3.5 3.5 0 0 1 0 4.95l-2 2a3.5 3.5 0 0 1-5.396-4.402.75.75 0 0 1 1.251.827 2 2 0 0 0 3.085 2.514l2-2a2 2 0 0 0 0-2.828.75.75 0 0 1 0-1.06Z"
									clip-rule="evenodd"
								/>
								<path
									fill-rule="evenodd"
									d="M7.086 9.975a.75.75 0 0 1-1.06 0 3.5 3.5 0 0 1 0-4.95l2-2a3.5 3.5 0 0 1 5.396 4.402.75.75 0 0 1-1.251-.827 2 2 0 0 0-3.085-2.514l-2 2a2 2 0 0 0 0 2.828.75.75 0 0 1 0 1.06Z"
									clip-rule="evenodd"
								/>
							</svg>
						</div>
					</Tooltip>
				{/if}

				{#if item.model?.info?.meta?.description}
					<Tooltip
						content={`${marked.parse(
							sanitizeResponseContent(item.model?.info?.meta?.description).replaceAll('\n', '<br>')
						)}`}
					>
						<div class=" translate-y-[1px]">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="w-4 h-4"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"
								/>
							</svg>
						</div>
					</Tooltip>
				{/if}
			</div>
		</div>
	</div>

	<div class="ml-auto pl-2 pr-1 flex items-center gap-1.5 shrink-0">
		{#if $user?.role === 'admin' && item.model.loaded}
			<Tooltip
				content={`${$i18n.t('Eject')}`}
				className="shrink-0 group-hover/item:opacity-100 opacity-0"
			>
				<button
					class="flex"
					aria-label={$i18n.t('Eject model')}
					on:click={(e) => {
						e.preventDefault();
						e.stopPropagation();
						unloadModelHandler(item.value);
					}}
				>
					<ArrowUpTray className="size-3" />
				</button>
			</Tooltip>
		{/if}

		{#if value === item.value}
			<div class="shrink-0">
				<Check className="size-5 text-hg-blue dark:text-gray-400" />
			</div>
		{/if}

		<ModelItemMenu
			bind:show={showMenu}
			model={item.model}
			{pinModelHandler}
			{deleteModelHandler}
			copyLinkHandler={() => {
				copyLinkHandler(item.model);
			}}
		>
			<button
				aria-label={`${$i18n.t('More Options')}`}
				class="flex"
				on:click={(e) => {
					e.preventDefault();
					e.stopPropagation();
					showMenu = !showMenu;
				}}
			>
				<EllipsisVertical className="size-5 text-hg-text-tertiary dark:text-gray-500" />
			</button>
		</ModelItemMenu>
	</div>
</button>
