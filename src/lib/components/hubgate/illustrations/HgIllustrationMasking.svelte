<script lang="ts">
	import HgIconPixelMask from '$lib/components/icons/HgIconPixelMask.svelte';
</script>

<div class="flex flex-col gap-2 w-full">
	<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-emphasis">Mask Sensitive Data</span>

	<div class="relative flex flex-col items-center w-full" style="gap: 8px;">
		<div class="bg-white border border-hg-border rounded-hg-md p-[10px] flex justify-between items-center w-full">
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-primary">
				Analyze contract for Johny Snow,<br />SSN: 846-983
			</span>
		</div>

		<div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-10 w-8 h-8 bg-hg-text-primary rounded-sm flex items-center justify-center">
			<HgIconPixelMask class="text-hg-bg-surface" />
		</div>

		<div class="bg-white border border-hg-border rounded-hg-md p-[10px] flex justify-between items-center w-full">
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-secondary">
				Analyze contract for <mark class="bg-amber-100 text-amber-800 rounded px-0.5 not-italic font-medium">[PERSON_1]</mark>, SSN: <mark class="bg-amber-100 text-amber-800 rounded px-0.5 not-italic font-medium">[US_SSN_1]</mark>.
			</span>
		</div>
	</div>
</div>
