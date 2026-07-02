<script lang="ts">
	import type { Component } from 'svelte';
	export let icon: Component;
	export let title: string;
	export let body: string;
	// Per-card colour family (icon tile bg + icon tint), matching Figma
	export let color: 'green' | 'blue' | 'orange' = 'orange';

	const tile: Record<string, string> = {
		green: 'bg-hg-success-50 text-hg-success-600',
		blue: 'bg-hg-info-bg text-hg-blue',
		orange: 'bg-hg-orange-50 text-hg-orange'
	};
</script>

<div
	class="bg-white border border-hg-border rounded-[24px] shadow-[0px_30px_60px_0px_rgba(240,201,150,0.07),0px_6px_16px_0px_rgba(108,108,109,0.06)] p-6 flex flex-col gap-2 h-full"
>
	<div class="w-[46px] h-[46px] rounded-[12px] flex items-center justify-center shrink-0 {tile[color]}">
		<svelte:component this={icon} class="h-6 w-auto" />
	</div>
	<h3 class="font-hg-heading font-medium text-lg leading-[1.4] text-hg-text-primary mt-2">{title}</h3>
	<p class="font-hg-body text-sm leading-[1.5] text-hg-text-tertiary">{body}</p>
</div>
