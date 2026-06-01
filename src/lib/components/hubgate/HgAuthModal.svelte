<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import HgAuthCard from './HgAuthCard.svelte';

	export let open = false;

	const dispatch = createEventDispatcher();

	const close = () => { open = false; };

	const onSuccess = (e: CustomEvent) => {
		dispatch('success', e.detail);
	};

	const onKeydown = (e: KeyboardEvent) => {
		if (e.key === 'Escape') close();
	};
</script>

<svelte:window on:keydown={onKeydown} />

{#if open}
	<div
		role="dialog"
		aria-modal="true"
		aria-label="Sign in"
		class="fixed inset-0 z-50 flex items-center justify-center p-4"
		on:click={close}
		on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') close(); }}
	>
		<div class="absolute inset-0 bg-black/60 backdrop-blur-sm"></div>

		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="relative z-10" on:click|stopPropagation>
			<HgAuthCard on:success={onSuccess} />
		</div>
	</div>
{/if}
