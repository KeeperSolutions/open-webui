<script lang="ts">
	import type { Component } from 'svelte';
	export let icon: Component;
	export let title: string;
	export let body: string;
</script>

<div class="flex gap-3">
	<div class="w-6 h-6 shrink-0 mt-0.5 flex items-center justify-center text-hg-orange">
		<svelte:component this={icon} class="text-hg-orange" />
	</div>
	<div class="flex flex-col gap-1">
		<span class="font-hg-heading font-medium text-lg leading-[1.3] text-hg-text-primary">{title}</span>
		<span class="font-hg-body text-sm leading-[1.5] text-hg-text-secondary">{body}</span>
	</div>
</div>
