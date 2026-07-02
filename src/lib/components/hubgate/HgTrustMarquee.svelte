<script lang="ts">
	import type { TrustPill } from '$lib/data/trust-centre';
	import HgTrustPill from './HgTrustPill.svelte';
	export let pills: TrustPill[];
</script>

<div class="w-full border-y border-hg-border bg-hg-bg-surface">
	<div class="max-w-[1248px] mx-auto px-4 sm:px-6 py-4 flex flex-wrap justify-center gap-x-8 gap-y-3">
		{#each pills as pill}
			<HgTrustPill icon={pill.icon} label={pill.label} />
		{/each}
	</div>
</div>
