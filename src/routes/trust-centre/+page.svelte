<script lang="ts">
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';

	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';

	onMount(() => {
		if (get(user)) { goto('/chat'); return; }
	});
</script>

<svelte:head>
	<title>Trust Centre — Hubgate</title>
</svelte:head>

{#if !$user}
	<div class="relative min-h-screen flex flex-col font-hg-body">
		<img
			src="/hubgate/hubgate-pixel-pattern.svg"
			alt=""
			aria-hidden="true"
			class="pointer-events-none"
			style="position:fixed;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:0"
		/>

		<div class="relative flex flex-col flex-1" style="z-index:1">
			<HgLandingHeader />

			<main class="flex-1 w-full max-w-[1248px] mx-auto px-4 sm:px-6 py-16 flex flex-col gap-24">
				<!-- sections added in later tasks -->
			</main>

			<HgLandingFooter />
		</div>
	</div>
{/if}
