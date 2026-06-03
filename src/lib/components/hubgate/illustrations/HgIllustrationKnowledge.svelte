<script lang="ts">
	import HgIconReview from '$lib/components/icons/HgIconReview.svelte';
	import HgIconChevronRight from '$lib/components/icons/HgIconChevronRight.svelte';
	import HgIconCursor from '$lib/components/icons/HgIconCursor.svelte';
</script>

<div class="relative flex flex-col gap-[6px] w-full items-center">
	<div class="absolute z-10" style="right: 24.76px; top: 52.5px;">
		<HgIconCursor class="text-hg-text-primary w-8 h-8" />
	</div>
	<div
		class="bg-white border border-hg-border rounded-hg-md p-2 flex items-center justify-between w-full"
	>
		<div class="flex items-center gap-1">
			<HgIconReview class="w-4 h-4 text-hg-text-primary" />
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-primary"
				>Attach knowledge</span
			>
		</div>
		<HgIconChevronRight class="text-hg-orange w-4 h-4 shrink-0" />
	</div>

	<div class="flex flex-col gap-[6px] w-full px-4">
		<div
			class="bg-white border border-hg-blue rounded-hg-md px-3 py-2 flex items-center gap-2 w-full shadow-[3px_3px_2px_0_rgba(111,117,128,0.11)]"
		>
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-primary"
				>Q4 Sales Report.pdf</span
			>
		</div>
		<div
			class="bg-white border border-hg-border rounded-hg-md px-3 py-2 flex items-center gap-2 w-full"
		>
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-primary"
				>Notion — Product Roadmap</span
			>
		</div>
		<div
			class="bg-white border border-hg-border rounded-hg-md px-3 py-2 flex items-center gap-2 w-full"
		>
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-primary"
				>Google Drive — Marketing</span
			>
		</div>
	</div>
</div>
