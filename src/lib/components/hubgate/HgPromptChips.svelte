<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { Component } from 'svelte';
	import HgIconDocument from '$lib/components/icons/HgIconDocument.svelte';
	import HgIconReview from '$lib/components/icons/HgIconReview.svelte';
	import HgIconImage from '$lib/components/icons/HgIconImage.svelte';
	import HgIconMeeting from '$lib/components/icons/HgIconMeeting.svelte';
	import HgIconEye from '$lib/components/icons/HgIconEye.svelte';

	const dispatch = createEventDispatcher();

	export let onSelect: ((data: { type: string; data: string }) => void) | null = null;

	const promptChips: { label: string; icon: Component }[] = [
		{ label: 'Draft a proposal', icon: HgIconDocument },
		{ label: 'Review a document', icon: HgIconReview },
		{ label: 'Generate an image', icon: HgIconImage },
		{ label: 'Summarize meeting', icon: HgIconMeeting },
		{ label: 'Analyse a contract', icon: HgIconEye }
	];
</script>

<div class="flex flex-wrap justify-center gap-2 w-full max-w-[880px]">
	{#each promptChips as chip}
		<button
			type="button"
			class="inline-flex items-center gap-2 h-8 px-4 bg-white dark:bg-gray-900 border border-hg-border dark:border-gray-700 rounded-full font-hg-body text-xs text-hg-text-secondary dark:text-gray-400 hover:border-hg-blue hover:text-hg-text-primary dark:hover:text-gray-100 transition-colors"
			on:click={() => onSelect ? onSelect({ type: 'prompt', data: chip.label }) : dispatch('open')}
		>
			<span class="hidden sm:contents"><svelte:component this={chip.icon} class="text-hg-text-primary dark:text-gray-400" /></span>
			{chip.label}
		</button>
	{/each}
</div>
