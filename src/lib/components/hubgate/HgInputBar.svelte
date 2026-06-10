<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import HgIconPlus from '$lib/components/icons/HgIconPlus.svelte';
	import HgIconSettings from '$lib/components/icons/HgIconSettings.svelte';
	import HgIconShield from '$lib/components/icons/HgIconShield.svelte';
	import HgIconToggleOn from '$lib/components/icons/HgIconToggleOn.svelte';
	import HgIconMic from '$lib/components/icons/HgIconMic.svelte';
	import HgIconWave from '$lib/components/icons/HgIconWave.svelte';
	const dispatch = createEventDispatcher();
</script>

<div
	class="w-full rounded-t-[24px] mb-0 sm:w-[calc(100%-2rem)] sm:max-w-[740px] sm:rounded-[24px] sm:mb-4 mx-auto bg-white border border-[#e7e5e4] shadow-[0px_4px_24px_-8px_rgba(0,0,0,0.06)] overflow-hidden cursor-pointer"
	role="button"
	tabindex="0"
	on:click={() => dispatch('open')}
	on:keydown={(e) => {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			dispatch('open');
		}
	}}
>
	<div class="px-5 py-5">
		<p class="font-hg-body text-sm text-[#a8a29e] text-left">
			Ask anything, or let us build your workspace...
		</p>
	</div>

	<div class="bg-white border-t border-[#f5f5f4] flex items-center justify-between px-2 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] sm:pb-2">
		<div class="flex items-center">
			<div class="w-9 h-9 flex items-center justify-center rounded-full hover:bg-[#f5f5f4] transition-colors">
				<HgIconPlus class="text-hg-text-primary" />
			</div>
			<div class="w-9 h-9 flex items-center justify-center rounded-full hover:bg-[#f5f5f4] transition-colors">
				<HgIconSettings class="text-hg-text-primary" />
			</div>
		</div>

		<div class="flex items-center gap-6">
			<div class="flex items-center gap-3">
				<div class="flex items-center gap-1">
					<HgIconShield class="text-hg-text-primary" />
					<span class="hidden sm:inline font-hg-body text-sm text-[#1c1917]">PII Masking</span>
				</div>
				<!-- Decorative: this is a fake teaser input; the whole bar opens the auth modal. -->
				<HgIconToggleOn class="text-hg-blue" />
			</div>

			<div class="w-9 h-9 flex items-center justify-center rounded-full hover:bg-[#f5f5f4] transition-colors">
				<HgIconMic class="text-hg-text-primary" />
			</div>

			<div class="w-9 h-9 bg-[#2563eb] rounded-full flex items-center justify-center">
				<HgIconWave class="text-white" />
			</div>
		</div>
	</div>
</div>
