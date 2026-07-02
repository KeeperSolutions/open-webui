<script lang="ts">
	import HgBadge from './HgBadge.svelte';
	import type { BadgeVariant } from '$lib/data/trust-centre';
	export let name: string;
	export let badge: { label: string; variant: BadgeVariant };
	export let body: string;
	export let bodyAccent: string | undefined = undefined;
</script>

<div
	class="bg-white border border-hg-border rounded-[24px] shadow-[0px_30px_60px_0px_rgba(240,201,150,0.07),0px_6px_16px_0px_rgba(108,108,109,0.06)] p-6 flex flex-col gap-4 h-full"
>
	<div class="flex items-center justify-between gap-3">
		<span class="font-hg-heading font-medium text-lg text-hg-text-primary">{name}</span>
		<HgBadge variant={badge.variant}>{badge.label}</HgBadge>
	</div>
	<p class="font-hg-body text-sm leading-[1.5] text-hg-text-tertiary">
		{body}{#if bodyAccent}<span class="text-red-600">{bodyAccent}</span>{/if}
	</p>
</div>
