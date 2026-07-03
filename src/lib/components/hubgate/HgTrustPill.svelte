<script lang="ts">
	import type { Component } from 'svelte';
	export let icon: Component;
	export let label: string;
</script>

<span class="inline-flex items-center gap-2 font-hg-body text-xs text-hg-text-secondary whitespace-nowrap">
	<svelte:component this={icon} class="text-hg-text-secondary" />
	{label}
</span>
