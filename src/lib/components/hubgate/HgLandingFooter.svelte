<script lang="ts">
	import { page } from '$app/stores';

	const links = [
		{ href: '/pricing', label: 'Pricing' },
		{ href: '/privacy', label: 'Privacy Policy' },
		{ href: '/terms', label: 'Terms of use' }
	];
</script>

<footer class="bg-hg-bg-surface border-t border-hg-border">
	<div class="max-w-7xl mx-auto px-8 py-3 sm:relative">
		<!-- Mobile layout -->
		<div class="flex flex-col items-center gap-4 sm:hidden">
			<div class="flex items-center gap-4">
				{#each links as link}
					<a
						href={link.href}
						class="font-hg-body text-xs transition-colors
							{$page.url.pathname === link.href
								? 'text-hg-text-primary font-semibold'
								: 'text-hg-text-tertiary hover:text-hg-text-secondary'}"
					>
						{link.label}
					</a>
				{/each}
			</div>
			<div class="flex flex-col items-center gap-2">
				<a href="/landing" aria-label="Hubgate home">
					<img src="/hubgate/hubgate-logo.svg" alt="Hubgate" width="90" height="18" />
				</a>
				<span class="font-hg-body text-xs text-hg-text-tertiary">Hosted in EU</span>
			</div>
		</div>

		<!-- Desktop layout -->
		<div class="hidden sm:flex items-center justify-between">
			<div class="flex items-center gap-4">
				<a href="/landing" aria-label="Hubgate home">
					<img src="/hubgate/hubgate-logo.svg" alt="Hubgate" width="90" height="18" />
				</a>
				<span class="font-hg-body text-xs text-hg-text-tertiary hidden lg:inline">© 2026 Hubgate. All rights reserved.</span>
			</div>

			<div class="absolute left-1/2 -translate-x-1/2 flex items-center gap-4">
				{#each links as link}
					<a
						href={link.href}
						class="font-hg-body text-xs transition-colors
							{$page.url.pathname === link.href
								? 'text-hg-text-primary font-semibold'
								: 'text-hg-text-tertiary hover:text-hg-text-secondary'}"
					>
						{link.label}
					</a>
				{/each}
			</div>

			<span class="font-hg-body text-xs text-hg-text-tertiary">Hosted in EU</span>
		</div>
	</div>
</footer>
