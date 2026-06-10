<script lang="ts">
	import { getContext } from 'svelte';

	import { user, theme } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { copyToClipboard } from '$lib/utils';
	import { resolveTheme } from '$lib/utils/theme';
	import ArrowUpTray from '$lib/components/icons/ArrowUpTray.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import ModelItemMenu from './ModelItemMenu.svelte';
	import EllipsisVertical from '$lib/components/icons/EllipsisVertical.svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	export let selectedModelIdx: number = -1;
	export let item: any = {};
	export let index: number = -1;
	export let value: string = '';

	export let unloadModelHandler: (modelValue: string) => void = () => {};
	export let pinModelHandler: (modelId: string) => void = () => {};

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
	aria-label={item.label}
	class="flex group/item w-full text-left select-none items-center rounded-xl px-3 py-2 outline-hidden transition-all duration-75 hover:bg-hg-bg-muted dark:hover:bg-gray-800 cursor-pointer {index ===
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
				alt="Model"
				class="rounded-full size-5 shrink-0"
				loading="lazy"
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
		</div>
	</div>

	<div class="ml-auto pl-2 pr-1 flex items-center gap-1.5 shrink-0">
		{#if $user?.role === 'admin' && item.model.owned_by === 'ollama' && item.model.ollama?.expires_at && new Date(item.model.ollama?.expires_at * 1000) > new Date()}
			<Tooltip
				content={`${$i18n.t('Eject')}`}
				className="shrink-0 group-hover/item:opacity-100 opacity-0"
			>
				<button
					class="flex"
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
