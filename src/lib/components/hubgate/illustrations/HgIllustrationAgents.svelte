<script lang="ts">
	import HgIconSettings from '$lib/components/icons/HgIconSettings.svelte';
	import HgIconDocument from '$lib/components/icons/HgIconDocument.svelte';
	import HgIconPolicy from '$lib/components/icons/HgIconPolicy.svelte';
</script>

<div class="flex flex-col gap-[6px] w-full">
	<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-emphasis">Agents</span>

	<!-- QA Reviewer — active -->
	<div class="bg-white border border-hg-text-emphasis rounded-hg-md p-[11px] flex items-center w-full">
		<div class="flex items-center gap-[10px] w-full">
			<div class="w-[6px] h-[6px] rounded-full bg-hg-success-600 shrink-0"></div>
			<div class="w-[22px] h-[22px] rounded-[4px] bg-hg-accent-400 flex items-center justify-center shrink-0">
				<HgIconSettings class="text-white w-4 h-4" />
			</div>
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-primary">QA Reviewer</span>
		</div>
	</div>

	<!-- Sales Drafter -->
	<div class="bg-white border border-hg-border rounded-hg-md p-[11px] flex items-center w-full">
		<div class="flex items-center gap-[10px] w-full">
			<div class="w-[6px] h-[6px] shrink-0"></div>
			<div class="w-[22px] h-[22px] rounded-[4px] bg-hg-blue flex items-center justify-center shrink-0">
				<HgIconDocument class="text-white w-4 h-4" />
			</div>
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-secondary">Sales Drafter</span>
		</div>
	</div>

	<!-- Policy Assistant -->
	<div class="bg-white border border-hg-border rounded-hg-md p-[11px] flex items-center w-full">
		<div class="flex items-center gap-[10px] w-full">
			<div class="w-[6px] h-[6px] shrink-0"></div>
			<div class="w-[22px] h-[22px] rounded-[4px] bg-hg-accent-700 flex items-center justify-center shrink-0">
				<HgIconPolicy class="text-white w-4 h-4" />
			</div>
			<span class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-secondary">Policy Assistant</span>
		</div>
	</div>
</div>
