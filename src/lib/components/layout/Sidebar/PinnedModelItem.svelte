<script lang="ts">
	import { getContext } from 'svelte';

	const i18n = getContext('i18n');

	import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
	import { mobile, theme } from '$lib/stores';
	import { resolveTheme } from '$lib/utils/theme';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import PinSlash from './icons/PinSlash.svelte';

	export let model = null;
	export let onClick = () => {};
	export let onUnpin = () => {};

	let mouseOver = false;
</script>

{#if model}
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div
		class=" flex justify-center text-gray-800 dark:text-gray-200 cursor-grab relative group"
		data-id={model?.id}
		on:mouseenter={() => {
			mouseOver = true;
		}}
		on:mouseleave={() => {
			mouseOver = false;
		}}
	>
		<a
			class="grow flex items-center space-x-2 rounded-xl px-2 py-[7px] group-hover:bg-gray-100 dark:group-hover:bg-gray-900 transition"
			href="/?model={encodeURIComponent(model.id)}"
			on:click={onClick}
			draggable="false"
		>
			<div class="self-center shrink-0">
				<img
					src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${model.id}&theme=${resolveTheme($theme)}&lang=${$i18n.language}`}
					class=" size-5 rounded-full -translate-x-[0.5px]"
					alt="logo"
					on:error={(e) => {
						e.currentTarget.src = '/favicon.png';
					}}
				/>
			</div>

			<div class="flex self-center translate-y-[0.5px]">
				<div class=" self-center text-[13px] leading-5 line-clamp-1">
					{model?.name ?? model.id}
				</div>
			</div>
		</a>

		<!-- Touch devices have no hover, so the unpin button stays out on mobile. -->
		{#if ($mobile || mouseOver) && onUnpin}
			<div class="absolute right-1 inset-y-0 mr-1.5 flex items-center">
				<Tooltip content={$i18n.t('Unpin')} className="flex items-center">
					<button
						class="flex size-5 items-center justify-center self-center hover:text-black dark:hover:text-white transition m-0"
						on:click={() => {
							onUnpin();
						}}
						type="button"
					>
						<PinSlash className="size-3.5" strokeWidth="1.5" />
					</button>
				</Tooltip>
			</div>
		{/if}
	</div>
{/if}
