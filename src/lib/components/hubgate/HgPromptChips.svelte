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

	const handleClick = (chip: { label: string; icon: Component }) =>
		onSelect ? onSelect({ type: 'prompt', data: chip.label }) : dispatch('open');
</script>

<!--
  Mobile: single scrollable row (overflow-x-auto, no wrap)
  Desktop (sm+): wrapping centered row
  A single set of chips is rendered — CSS switches the layout, avoiding DOM duplication.
-->
<div class="w-full sm:flex sm:flex-wrap sm:justify-center sm:max-w-[880px] overflow-x-auto sm:overflow-x-visible scrollbar-none">
	<div class="flex gap-2 px-3 sm:px-0 pb-1 sm:pb-0 sm:flex-wrap sm:justify-center" style="width: max-content; min-width: 100%;">
		{#each promptChips as chip}
			<button
				type="button"
				class="inline-flex items-center gap-2 h-8 px-4 bg-white dark:bg-gray-900 border border-hg-border dark:border-gray-700 rounded-full font-hg-body text-xs text-hg-text-secondary dark:text-gray-400 hover:border-hg-blue hover:text-hg-text-primary dark:hover:text-gray-100 transition-colors shrink-0"
				on:click={() => handleClick(chip)}
			>
				<svelte:component this={chip.icon} class="text-hg-text-primary dark:text-gray-400" />
				{chip.label}
			</button>
		{/each}
	</div>
</div>
