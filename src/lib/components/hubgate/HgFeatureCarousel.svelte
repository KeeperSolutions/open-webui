<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { features } from '$lib/data/landing-features';
	import HgFeatureCard from '$lib/components/hubgate/HgFeatureCard.svelte';
	import HgIconLock from '$lib/components/icons/HgIconLock.svelte';

	const dispatch = createEventDispatcher();
	let activeFeature = 0;
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
					on:open={() => dispatch('open')}
					on:dismiss={() => dispatch('dismiss')}
				>
					<svelte:fragment slot="illustration">
						{#if i === 0}
							<div class="w-24 h-24 [&>svg]:w-full! [&>svg]:h-full!">
								<HgIconLock class="text-hg-success-400" />
							</div>
							<div class="bg-white border border-hg-text-secondary rounded-full px-4 py-1.5">
								<span class="font-hg-body text-xs text-hg-text-secondary">No data used to train AI models</span>
							</div>
						{/if}
					</svelte:fragment>
				</HgFeatureCard>
			{/if}
		{/each}
	{/key}
</div>
