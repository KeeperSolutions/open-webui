<script lang="ts">
	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';
</script>

<svelte:head>
	<title>Pricing — Hubgate</title>
</svelte:head>

<div class="min-h-screen flex flex-col font-hg-body bg-hg-bg-surface">
	<HgLandingHeader />

	<main class="flex-1 max-w-3xl mx-auto w-full px-6 py-16">
		<div class="mb-10">
			<h1 class="font-hg-heading font-bold text-3xl text-hg-text-primary mb-2">
				HubGate.io Pricing
			</h1>
			<p class="text-sm text-hg-text-tertiary">Under development</p>
		</div>

		<hr class="border-hg-border mb-10" />
	</main>

	<HgLandingFooter />
</div>
