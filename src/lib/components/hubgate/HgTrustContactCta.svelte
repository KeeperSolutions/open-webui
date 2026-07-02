<script lang="ts">
	import HgButton from './HgButton.svelte';
	import type { TrustContact } from '$lib/data/trust-centre';
	export let title: string;
	export let body: string;
	export let buttonLabel: string;
	export let buttonHref: string;
	export let contacts: TrustContact[];
</script>

<div class="rounded-[24px] bg-hg-text-primary text-white p-8 sm:p-12 grid grid-cols-1 md:grid-cols-2 gap-8 items-center">
	<div class="flex flex-col gap-4 items-start">
		<h2 class="font-hg-heading font-bold text-[clamp(24px,3vw,28px)] leading-[1.2]">{title}</h2>
		<p class="font-hg-body text-base leading-[1.6] text-white/80">{body}</p>
		<a href={buttonHref}><HgButton variant="primary" size="md">{buttonLabel}</HgButton></a>
	</div>
	<div class="flex flex-col gap-3">
		{#each contacts as c}
			<div class="rounded-[16px] border border-white/15 bg-white/5 p-5 flex flex-col gap-1">
				<span class="font-hg-body text-sm text-white/70">{c.label}</span>
				<a href={`mailto:${c.email}`} class="font-hg-body text-lg text-white hover:underline">{c.email}</a>
			</div>
		{/each}
	</div>
</div>
