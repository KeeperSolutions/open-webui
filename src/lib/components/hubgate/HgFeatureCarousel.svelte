<script lang="ts">
	import { createEventDispatcher, onDestroy, onMount } from 'svelte';
	import { fade } from 'svelte/transition';
	import { features } from '$lib/data/landing-features';
	import HgFeatureCard from '$lib/components/hubgate/HgFeatureCard.svelte';

	const dispatch = createEventDispatcher();
	let activeFeature = 0;
	let paused = false;

	let touchStartX = 0;
	let touchStartY = 0;

	function next() {
		activeFeature = (activeFeature + 1) % features.length;
	}

	function prev() {
		activeFeature = (activeFeature - 1 + features.length) % features.length;
	}

	function onTouchStart(e: TouchEvent) {
		touchStartX = e.touches[0].clientX;
		touchStartY = e.touches[0].clientY;
	}

	function onTouchEnd(e: TouchEvent) {
		const dx = e.changedTouches[0].clientX - touchStartX;
		const dy = e.changedTouches[0].clientY - touchStartY;
		if (Math.abs(dx) < 30 || Math.abs(dx) < Math.abs(dy)) return;
		if (dx < 0) next(); else prev();
	}

	let interval: ReturnType<typeof setInterval>;

	function startAutoplay() {
		interval = setInterval(() => {
			if (!paused) next();
		}, 4000);
	}

	onMount(startAutoplay);
	onDestroy(() => clearInterval(interval));
</script>

<div
	role="region"
	aria-label="Feature slides"
	class="w-full max-w-[740px] px-0 sm:px-8 pb-8"
	on:mouseenter={() => (paused = true)}
	on:mouseleave={() => (paused = false)}
	on:touchstart={onTouchStart}
	on:touchend={onTouchEnd}
>
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

	<div class="sm:min-h-[224px]">
		{#key activeFeature}
			{#each features as feature, i}
				{#if i === activeFeature}
					<div in:fade={{ duration: 300 }}>
						<HgFeatureCard
							title={feature.title}
							description={feature.description}
							illustration={feature.illustration}
							on:open={() => dispatch('open')}
							on:dismiss={() => dispatch('dismiss')}
						/>
					</div>
				{/if}
			{/each}
		{/key}
	</div>
</div>
