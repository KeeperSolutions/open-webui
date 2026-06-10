<script lang="ts">
	import { handleAuthSuccess } from '$lib/utils/auth';

	import HgButton from './HgButton.svelte';
	import HgAuthModal from './HgAuthModal.svelte';
	import HgMobileSidebar from './HgMobileSidebar.svelte';
	import HgIconHamburger from '$lib/components/icons/HgIconHamburger.svelte';

	let menuOpen = false;
	let showModal = false;

	const onSuccess = async (e: CustomEvent) => {
		showModal = false;
		await handleAuthSuccess(e.detail);
	};
</script>

<header class="sticky top-0 z-50 bg-hg-bg-surface border-b border-hg-border w-full">
	<div class="max-w-7xl mx-auto px-3 sm:px-8 h-[57px] sm:h-[65px] flex items-center justify-between">

		<!-- Mobile left: hamburger + logo -->
		<div class="flex items-center gap-4 sm:hidden">
			<button
				type="button"
				aria-label={menuOpen ? 'Close menu' : 'Open menu'}
				class="text-hg-text-primary"
				on:click={() => (menuOpen = !menuOpen)}
			>
				{#if menuOpen}
					<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none">
						<path d="M5 5L15 15M15 5L5 15" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
				{:else}
					<HgIconHamburger />
				{/if}
			</button>
			<a href="/" aria-label="Hubgate home">
				<img src="/hubgate/hubgate-logo.svg" alt="Hubgate" width="90" height="18" />
			</a>
		</div>

		<!-- Desktop left: logo -->
		<a href="/" aria-label="Hubgate home" class="hidden sm:block">
			<img src="/hubgate/hubgate-logo.svg" alt="Hubgate" width="120" height="24" />
		</a>

		<!-- Right: Sign In (sm+) + Get Started (all) -->
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

<HgMobileSidebar
	open={menuOpen}
	on:close={() => (menuOpen = false)}
	on:signin={() => {
		menuOpen = false;
		showModal = true;
	}}
/>

<HgAuthModal bind:open={showModal} on:success={onSuccess} />
