<script lang="ts">
	import HgIconCheck from '$lib/components/icons/HgIconCheck.svelte';
	export let title: string;
	export let body: string;
</script>

<div class="flex gap-3">
	<div
		class="w-6 h-6 shrink-0 mt-px flex items-center justify-center rounded-[12px] bg-hg-success-50 text-hg-success-700"
	>
		<HgIconCheck class="w-4 h-4" />
	</div>
	<div class="flex flex-col gap-1">
		<span class="font-hg-heading font-medium text-lg leading-[1.4] text-hg-text-primary">{title}</span>
		<span class="font-hg-body text-sm leading-[1.5] text-hg-text-tertiary">{body}</span>
	</div>
</div>
