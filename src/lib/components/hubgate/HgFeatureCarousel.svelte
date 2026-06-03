<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { features } from '$lib/data/landing-features';
	import HgFeatureCard from '$lib/components/hubgate/HgFeatureCard.svelte';

	const dispatch = createEventDispatcher();
	let activeFeature = 2;
</script>

<div class="w-full max-w-[740px] px-8 pb-8">
	<div
		class="flex items-center justify-center gap-1 mb-3"
		role="tablist"
		aria-label="Feature slides"
	>
		{#each Array.from({ length: features.length }, (_, i) => i) as i}
			<button
				role="tab"
				type="button"
				aria-selected={activeFeature === i}
				aria-label="Slide {i + 1}"
				class="h-2 rounded-full transition-all duration-200 {activeFeature === i
					? 'w-5 bg-hg-text-primary'
					: 'w-2 bg-hg-border'}"
				on:click={() => (activeFeature = i)}
			></button>
		{/each}
	</div>

	{#key activeFeature}
		{#each features as feature, i}
			{#if i === activeFeature}
				<HgFeatureCard
					title={feature.title}
					description={feature.description}
					illustration={feature.illustration}
					on:open={() => dispatch('open')}
					on:dismiss={() => dispatch('dismiss')}
				/>
			{/if}
		{/each}
	{/key}
</div>
