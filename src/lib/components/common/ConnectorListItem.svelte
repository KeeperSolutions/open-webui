<script lang="ts">
	export let icon: any = undefined;
	export let name: string;
	export let subtext: string = '';
</script>

<div class="flex items-center gap-2.5 min-w-0">
	{#if icon}
		<svelte:component this={icon} />
	{/if}
	<div class="min-w-0">
		<div class="text-sm text-gray-900 dark:text-white">{name}</div>
		<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">{subtext}</div>
	</div>
</div>
