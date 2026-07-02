<script lang="ts">
	import type { Component } from 'svelte';
	export let icon: Component;
	export let title: string;
	export let body: string;
</script>

<div class="bg-white border border-hg-border-subtle rounded-[18px] shadow-[0px_2px_12px_0px_rgba(0,0,0,0.02)] p-6 flex flex-col gap-3 h-full">
	<div class="w-[46px] h-[46px] rounded-[12px] bg-hg-orange-50 flex items-center justify-center">
		<svelte:component this={icon} class="text-hg-orange" />
	</div>
	<h3 class="font-hg-heading font-medium text-lg leading-[1.4] text-hg-text-primary mt-2">{title}</h3>
	<p class="font-hg-body text-sm leading-[1.5] text-hg-text-secondary">{body}</p>
</div>
