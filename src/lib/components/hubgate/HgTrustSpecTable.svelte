<script lang="ts">
	import HgBadge from './HgBadge.svelte';
	import type { SpecRow, BadgeVariant } from '$lib/data/trust-centre';
	export let title: string;
	export let rows: SpecRow[];
	export let headerBadge: { label: string; variant: BadgeVariant } | undefined = undefined;
</script>

<div class="bg-white border border-hg-border-subtle rounded-[18px] shadow-[0px_2px_12px_0px_rgba(0,0,0,0.02)] overflow-hidden">
	<div class="flex items-center justify-between gap-3 px-5 h-[60px] border-b border-hg-border-subtle">
		<span class="font-hg-heading font-bold text-lg text-hg-text-primary">{title}</span>
		{#if headerBadge}<HgBadge variant={headerBadge.variant}>{headerBadge.label}</HgBadge>{/if}
	</div>
	{#each rows as row, i}
		<div class="flex items-center justify-between gap-3 px-5 h-[61px] {i < rows.length - 1 ? 'border-b border-hg-border-subtle' : ''}">
			<span class="font-hg-body text-sm text-hg-text-primary">{row.label}</span>
			{#if row.badge}
				<HgBadge variant={row.badge.variant}>{row.badge.label}</HgBadge>
			{:else if row.value}
				<span class="font-hg-body text-sm text-hg-text-secondary">{row.value}</span>
			{/if}
		</div>
	{/each}
</div>
