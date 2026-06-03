<script lang="ts">
	import { handleAuthSuccess } from '$lib/utils/auth';

	import HgButton from './HgButton.svelte';
	import HgAuthModal from './HgAuthModal.svelte';

	let showModal = false;

	const onSuccess = async (e: CustomEvent) => {
		showModal = false;
		await handleAuthSuccess(e.detail);
	};
</script>

<header class="sticky top-0 z-50 bg-hg-bg-surface border-b border-hg-border w-full">
	<div class="max-w-7xl mx-auto px-8 h-[65px] flex items-center justify-between">
		<a href="/" aria-label="Hubgate home">
			<img src="/hubgate/hubgate-logo.svg" alt="Hubgate" width="120" height="24" />
		</a>
		<nav class="flex items-center gap-2">
			<button
				type="button"
				class="hidden sm:inline-flex h-10 px-4 items-center font-hg-body font-semibold text-sm text-hg-text-secondary hover:text-hg-text-primary transition-colors rounded-hg-full"
				on:click={() => (showModal = true)}
			>
				Sign In
			</button>
			<HgButton variant="primary" size="md" on:click={() => (showModal = true)}>
				Get Started
			</HgButton>
		</nav>
	</div>
</header>

<HgAuthModal bind:open={showModal} on:success={onSuccess} />
