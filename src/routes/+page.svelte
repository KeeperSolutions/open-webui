<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { WEBUI_NAME, user } from '$lib/stores';
	import { handleAuthSuccess } from '$lib/utils/auth';

	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';
	import HgHeroSection from '$lib/components/hubgate/HgHeroSection.svelte';
	import HgInputBar from '$lib/components/hubgate/HgInputBar.svelte';
	import HgPromptChips from '$lib/components/hubgate/HgPromptChips.svelte';
	import HgFeatureCarousel from '$lib/components/hubgate/HgFeatureCarousel.svelte';
	import HgHiddenFeatures from '$lib/components/hubgate/HgHiddenFeatures.svelte';
	import HgAuthModal from '$lib/components/hubgate/HgAuthModal.svelte';

	let showModal = false;
	let carouselDismissed = false;

	onMount(() => {
		if ($user) goto('/chat');
	});

	const onSuccess = async (e: CustomEvent) => {
		showModal = false;
		await handleAuthSuccess(e.detail);
	};
</script>

<svelte:head>
	<title>{$WEBUI_NAME}</title>
</svelte:head>

{#if $user}{:else}
<div class="relative min-h-screen flex flex-col font-hg-body">
	<img
		src="/hubgate/hubgate-pixel-pattern.svg"
		alt="Background pixel pattern"
		aria-hidden="true"
		class="pointer-events-none"
		style="position:fixed;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:0"
	/>
	<div class="relative flex flex-col flex-1" style="z-index:1">
		<HgLandingHeader />

		<div class="flex-1 flex flex-col items-center">
			<div class="flex-1"></div>
			<HgHeroSection />
			<HgInputBar on:open={() => (showModal = true)} />
			<HgPromptChips on:open={() => (showModal = true)} />
			{#if carouselDismissed}
				<div class="flex-1"></div>
				<div class="pb-8 w-full flex justify-center">
					<HgHiddenFeatures on:open={() => (carouselDismissed = false)} />
				</div>
			{:else}
				<HgFeatureCarousel
					on:open={() => (showModal = true)}
					on:dismiss={() => (carouselDismissed = true)}
				/>
				<div class="flex-1"></div>
			{/if}
		</div>

		<HgLandingFooter />
	</div>
</div>

<HgAuthModal bind:open={showModal} on:success={onSuccess} />
{/if}
