<script lang="ts">
	export let variant: 'default' | 'primary' = 'default';
</script>

<button
	type="button"
	on:click
	class="rounded-full px-4 py-2 text-[12.5px] font-semibold leading-[1.55] whitespace-nowrap transition font-['Inter'] {variant ===
	'primary'
		? 'bg-pii-blue text-pii-white'
		: 'border border-pii-line bg-pii-white text-pii-ink'}"
>
	<slot />
</button>
