<script lang="ts">
	import HgIconShield from '$lib/components/icons/HgIconShield.svelte';
</script>

<div class="flex flex-col gap-4 items-center w-full">
	<div class="border border-hg-blue rounded-hg-full px-3 py-1 flex items-center gap-1 bg-blue-50">
		<HgIconShield class="text-hg-blue w-4 h-4 shrink-0" />
		<span class="font-hg-body text-[14px] font-semibold leading-[140%] text-hg-blue whitespace-nowrap">Logs: Immutable</span>
	</div>

	<div class="flex gap-1 w-full max-w-[276px]">
		<div class="flex-1 bg-white border border-hg-border rounded-hg-md px-3 py-2">
			<div class="font-hg-heading text-[18px] font-medium leading-[140%] text-hg-text-primary">247</div>
			<div class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-secondary">Active users</div>
		</div>
		<div class="flex-1 bg-white border border-hg-border rounded-hg-md px-3 py-2">
			<div class="font-hg-heading text-[18px] font-medium leading-[140%] text-hg-text-primary">18</div>
			<div class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-secondary">Active policies</div>
		</div>
		<div class="w-[88px] bg-white border border-hg-border rounded-hg-md px-3 py-2">
			<div class="font-hg-heading text-[18px] font-medium leading-[140%] text-hg-text-primary">€145</div>
			<div class="font-hg-body text-[12px] font-normal leading-[140%] text-hg-text-secondary">Usage this month</div>
		</div>
	</div>
</div>
