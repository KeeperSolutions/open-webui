<script lang="ts">
	export let variant: 'default' | 'primary' = 'default';
	/**
	 * Blocks the button for every input device, not just the mouse.
	 *
	 * ⚠️ The alternative — a wrapper carrying `pointer-events-none` — looks the
	 * same and is not the same: it stops the pointer and leaves the button in the
	 * tab order, so Enter still reaches the handler and a screen reader still
	 * announces the control as available. Mirrors `parts/Toggle.svelte`, which
	 * already takes the prop.
	 */
	export let disabled = false;
</script>

<button
	type="button"
	on:click
	{disabled}
	class="rounded-full px-4 py-2 text-[12.5px] font-semibold leading-[1.55] whitespace-nowrap transition font-['Inter'] disabled:cursor-not-allowed disabled:opacity-40 {variant ===
	'primary'
		? 'bg-pii-blue text-pii-white'
		: 'border border-pii-line bg-pii-white text-pii-ink'}"
>
	<slot />
</button>
