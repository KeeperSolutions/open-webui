<script lang="ts">
	import { WEBUI_NAME } from '$lib/stores';
	import HgIconLock from '$lib/components/icons/HgIconLock.svelte';
</script>

<div class="flex flex-col items-center text-center px-8 pt-6 sm:pt-16 pb-0">
	<div class="mb-8 hidden sm:block">
		<img src="/hubgate/hubgate-logo.svg" alt={$WEBUI_NAME} width="120" height="24" />
	</div>

	<div
		class="mb-6 inline-flex items-center gap-1.5 bg-white border border-[#57534e] rounded-[87px] px-4 py-1.5"
	>
		<HgIconLock class="text-hg-success-600" />
		<span class="font-hg-body text-xs text-[#57534e]">No data used to train AI models</span>
	</div>

	<h1
		class="font-hg-heading font-bold text-[clamp(28px,4vw,44px)] leading-[1.2] text-[#1c1917] mb-4 w-full max-w-[960px]"
	>
		Leading AI models. <span class="text-hg-orange">Privately governed.</span>
	</h1>

	<p class="font-hg-body text-base text-[#57534e] leading-[1.6] mb-8 max-w-[487px]">
		Privately access leading AI models — with your data in your control.
	</p>
</div>
