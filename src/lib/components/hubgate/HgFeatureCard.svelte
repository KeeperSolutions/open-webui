<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import HgIconChevronRight from '$lib/components/icons/HgIconChevronRight.svelte';
	import HgIconClose from '$lib/components/icons/HgIconClose.svelte';

	export let title: string;
	export let description: string;

	const dispatch = createEventDispatcher();
</script>

<div
	class="bg-white border border-hg-border-subtle rounded-[18px] shadow-[0px_2px_12px_0px_rgba(0,0,0,0.02)] overflow-hidden flex h-[224px]"
>
	<div class="relative shrink-0 overflow-hidden bg-[#eff6ff]" style="width:276px">
		<div class="absolute inset-0" aria-hidden="true">
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:40px;top:167px"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:0;top:119px"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:253px;top:0"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:213px;top:40px"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:252px;top:92px"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:87px;top:18px"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:47px;top:58px"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:87px;top:98px"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:222px;top:162px"></div>
			<div class="absolute w-10 h-10 bg-[#dbeafe]" style="left:182px;top:202px"></div>
		</div>

		<div class="relative flex flex-col items-center justify-center h-full gap-4 p-5">
			<slot name="illustration" />
		</div>
	</div>

	<div class="flex-1 flex flex-col justify-between p-8 text-left relative">
		<button
			type="button"
			class="absolute top-4 right-4 hover:opacity-70 transition-opacity"
			aria-label="Dismiss"
			on:click={() => dispatch('dismiss')}
		>
			<HgIconClose class="text-hg-text-tertiary" />
		</button>

		<div class="flex flex-col gap-2">
			<h3 class="font-hg-heading font-bold text-[22px] text-hg-text-primary leading-[1.3]">
				{title}
			</h3>
			<p class="font-hg-body text-sm text-hg-text-secondary leading-[1.4]">
				{description}
			</p>
		</div>

		<button
			type="button"
			class="inline-flex items-center gap-1 font-hg-body font-semibold text-sm text-hg-text-primary hover:text-hg-blue transition-colors"
			on:click={() => dispatch('open')}
		>
			Get Started
			<HgIconChevronRight class="text-hg-orange" />
		</button>
	</div>
</div>
