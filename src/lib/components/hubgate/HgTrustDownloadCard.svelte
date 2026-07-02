<script lang="ts">
	import type { Component } from 'svelte';
	import HgIconDownload from '$lib/components/icons/HgIconDownload.svelte';
	export let icon: Component;
	export let title: string;
	export let meta: string;
	export let href: string;
</script>

<a
	{href}
	class="bg-white border border-hg-border rounded-[24px] shadow-[0px_30px_60px_0px_rgba(240,201,150,0.07),0px_6px_16px_0px_rgba(108,108,109,0.06)] p-6 flex items-center justify-between gap-2 hover:border-hg-blue transition-colors"
>
	<div class="flex items-center gap-2 min-w-0">
		<div class="w-[46px] h-[46px] rounded-[12px] bg-hg-info-bg text-hg-blue flex items-center justify-center shrink-0">
			<svelte:component this={icon} class="h-6 w-auto" />
		</div>
		<div class="flex flex-col gap-1 min-w-0">
			<span class="font-hg-heading font-medium text-lg text-hg-text-primary">{title}</span>
			<span class="font-hg-body text-sm text-hg-text-tertiary">{meta}</span>
		</div>
	</div>
	<HgIconDownload class="text-hg-blue shrink-0 w-6 h-6" />
</a>
