<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { page } from '$app/stores';

	export let open = false;

	const dispatch = createEventDispatcher<{ close: void; signin: void }>();

	const links = [
		{ href: '/pricing', label: 'Pricing' },
		{ href: '/trust-centre', label: 'Trust Centre' },
		{ href: '/privacy', label: 'Privacy Policy' },
		{ href: '/terms', label: 'Terms of Use' }
	];

	function handleClose() {
		dispatch('close');
	}

	function handleSignin() {
		dispatch('signin');
	}
</script>

{#if open}
	<button
		type="button"
		class="fixed inset-0 z-40 w-full h-full bg-black/20 sm:hidden cursor-default"
		aria-label="Close menu"
		on:click={handleClose}
	></button>
{/if}

<!-- 140px is roughly the width of the Get Started button (the gap left on the right) -->
<div
	class="fixed top-[57px] left-0 h-[calc(100vh-57px)] w-[calc(100vw-140px)] z-50
		bg-hg-bg-surface border-r border-hg-border
		flex flex-col justify-between overflow-hidden
		transform transition-transform duration-200 ease-in-out sm:hidden
		{open ? 'translate-x-0' : '-translate-x-full'}"
	inert={!open}
	aria-hidden={!open}
>
	<div class="flex flex-col items-start gap-6 self-stretch p-3">
		<nav class="flex flex-col gap-2 self-stretch">
			{#each links as link}
				<a
					href={link.href}
					class="font-hg-body text-sm text-hg-text-primary py-1
						{$page.url.pathname === link.href ? 'font-semibold' : ''}"
					aria-current={$page.url.pathname === link.href ? 'page' : undefined}
					on:click={handleClose}
				>
					{link.label}
				</a>
			{/each}
		</nav>

		<button
			type="button"
			class="self-start h-10 px-4 rounded-hg-full border border-hg-border
				font-hg-body font-semibold text-sm text-hg-text-secondary
				hover:text-hg-text-primary transition-colors"
			on:click={handleSignin}
		>
			Sign In
		</button>
	</div>

	<div class="p-3 flex flex-col gap-1 pb-6">
		<span class="font-hg-body text-xs text-hg-text-tertiary">Hosted in EU</span>
		<span class="font-hg-body text-xs text-hg-text-tertiary"
			>© 2026 Hubgate. All rights reserved.</span
		>
	</div>
</div>
