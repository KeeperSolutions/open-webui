<script lang="ts">
	import HgBadge from './HgBadge.svelte';
	import type { BadgeVariant } from '$lib/data/trust-centre';
	export let name: string;
	export let badge: { label: string; variant: BadgeVariant };
	export let body: string;
</script>

<div class="bg-white border border-hg-border-subtle rounded-[18px] shadow-[0px_2px_12px_0px_rgba(0,0,0,0.02)] p-6 flex flex-col gap-3 h-full">
	<div class="flex items-center justify-between gap-3">
		<span class="font-hg-heading font-bold text-lg text-hg-text-primary">{name}</span>
		<HgBadge variant={badge.variant}>{badge.label}</HgBadge>
	</div>
	<p class="font-hg-body text-sm leading-[1.5] text-hg-text-secondary">{body}</p>
</div>
