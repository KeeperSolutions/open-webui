<script lang="ts">
	// Simplified pixel flags used in the Model catalogue, matching Figma
	export let flag: 'fr' | 'eu' | 'us';
</script>

<span
	class="inline-flex h-[18px] w-[26px] shrink-0 items-stretch overflow-hidden rounded-[3px] border border-black/[0.06] p-px"
	aria-hidden="true"
>
	{#if flag === 'fr'}
		<span class="flex-1 bg-[#0055a4]"></span><span class="flex-1 bg-white"></span><span
			class="flex-1 bg-[#ef4135]"
		></span>
	{:else if flag === 'us'}
		<span class="flex-1 bg-[#b22234]"></span><span class="flex-1 bg-white"></span><span
			class="flex-1 bg-[#b22234]"
		></span>
	{:else}
		<span class="flex flex-1 items-center justify-center bg-[#003399] text-[10px] leading-none text-[#ffcc00]"
			>★</span
		>
	{/if}
</span>
