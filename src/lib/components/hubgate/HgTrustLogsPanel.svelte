<script lang="ts">
	export let stats: { value: string; label: string }[];
</script>

<div class="rounded-[18px] bg-hg-info-bg border border-hg-border-subtle p-8 flex flex-col gap-6 h-full justify-center">
	<span class="inline-flex items-center gap-2 self-start px-3 h-8 rounded-hg-full bg-white border border-hg-border font-hg-body text-sm text-hg-text-primary">Logs · Immutable</span>
	<div class="grid grid-cols-3 gap-4">
		{#each stats as s}
			<div class="flex flex-col gap-1">
				<span class="font-hg-heading font-bold text-3xl text-hg-text-primary">{s.value}</span>
				<span class="font-hg-body text-xs text-hg-text-secondary">{s.label}</span>
			</div>
		{/each}
	</div>
</div>
