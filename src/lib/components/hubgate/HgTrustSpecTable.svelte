<script lang="ts">
	import HgBadge from './HgBadge.svelte';
	import HgTrustFlag from './HgTrustFlag.svelte';
	import type { SpecRow, BadgeVariant } from '$lib/data/trust-centre';
	export let title: string;
	export let rows: SpecRow[];
	export let headerBadge: { label: string; variant: BadgeVariant } | undefined = undefined;
</script>

<div
	class="bg-white border border-hg-border-subtle rounded-[24px] overflow-hidden shadow-[0px_30px_60px_-24px_rgba(240,201,150,0.07),0px_6px_16px_0px_rgba(108,108,109,0.06)]"
>
	<div class="flex items-center justify-between gap-3 px-5 py-4 border-b border-hg-border-subtle">
		<span class="font-hg-heading font-medium text-lg text-hg-text-primary">{title}</span>
		{#if headerBadge}<HgBadge variant={headerBadge.variant}>{headerBadge.label}</HgBadge>{/if}
	</div>
	{#each rows as row, i}
		<div
			class="flex items-center justify-between gap-3 px-5 py-4 {i < rows.length - 1
				? 'border-b border-hg-border-subtle'
				: ''}"
		>
			<span class="flex items-center gap-3 min-w-0">
				{#if row.flag}<HgTrustFlag flag={row.flag} />{/if}
				<span class="font-hg-body text-sm text-hg-text-primary truncate">{row.label}</span>
			</span>
			{#if row.badge}
				<HgBadge variant={row.badge.variant}>{row.badge.label}</HgBadge>
			{:else if row.value}
				<span class="font-hg-body text-sm text-hg-text-secondary whitespace-nowrap">{row.value}</span>
			{/if}
		</div>
	{/each}
</div>
