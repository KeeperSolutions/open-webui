<script lang="ts">
	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';
</script>

<svelte:head>
	<title>Privacy Policy — Hubgate</title>
</svelte:head>

<div class="min-h-screen flex flex-col font-hg-body bg-hg-bg-surface">
	<HgLandingHeader />

	<main class="flex-1 max-w-3xl mx-auto w-full px-6 py-16">
		<div class="mb-10">
			<h1 class="font-hg-heading font-bold text-3xl text-hg-text-primary mb-2">
				HubGate.io Privacy Policy
			</h1>
			<a
				href="https://keepersolutions.com/privacy-policy/"
				target="_blank"
				rel="noopener noreferrer"
				class="text-sm text-hg-blue hover:underline"
			>
				Privacy Policy of Keeper Solutions, Inc.
			</a>
		</div>

		<hr class="border-hg-border mb-10" />
	</main>

	<HgLandingFooter />
</div>
