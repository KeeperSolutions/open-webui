<script lang="ts">
	import type { Component } from 'svelte';
	export let icon: Component;
	export let title: string;
	export let meta: string;
	export let href: string;
</script>

<a
	{href}
	class="bg-white border border-hg-border-subtle rounded-[18px] shadow-[0px_2px_12px_0px_rgba(0,0,0,0.02)] p-6 flex items-center gap-4 hover:border-hg-border transition-colors"
>
	<div class="w-[46px] h-[46px] rounded-[12px] bg-hg-orange-50 flex items-center justify-center shrink-0">
		<svelte:component this={icon} class="text-hg-orange" />
	</div>
	<div class="flex flex-col gap-1 flex-1">
		<span class="font-hg-heading font-medium text-lg text-hg-text-primary">{title}</span>
		<span class="font-hg-body text-xs text-hg-text-tertiary">{meta}</span>
	</div>
	<span class="text-hg-text-tertiary shrink-0" aria-hidden="true">↓</span>
</a>
