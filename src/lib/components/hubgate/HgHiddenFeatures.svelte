<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import HgIconOpenBox from '$lib/components/icons/HgIconOpenBox.svelte';
	import HgIconChevronRight from '$lib/components/icons/HgIconChevronRight.svelte';

	const dispatch = createEventDispatcher();
</script>

<button
	type="button"
	class="bg-white border border-hg-border-subtle flex gap-3 items-center justify-center px-5 py-4 rounded-full hover:bg-hg-bg-muted transition-colors mx-auto"
	on:click={() => dispatch('open')}
>
	<div class="flex items-center gap-1 shrink-0">
		<HgIconOpenBox class="text-hg-blue w-6 h-6 shrink-0 -translate-y-0.5" />
		<span
			class="font-hg-heading font-medium text-[18px] text-hg-text-primary leading-[1.4] whitespace-nowrap"
		>
			What can Hubgate do?
		</span>
	</div>
	<div class="-rotate-90 [&>svg]:w-5 [&>svg]:h-5">
		<HgIconChevronRight class="text-hg-text-primary" />
	</div>
</button>
