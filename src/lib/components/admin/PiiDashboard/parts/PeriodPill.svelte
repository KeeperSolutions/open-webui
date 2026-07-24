<script lang="ts">
	export let active = false;
</script>

<button
	type="button"
	on:click
	class="rounded-full px-4 py-[7px] text-[13px] font-medium leading-[1.55] whitespace-nowrap transition font-['Inter'] {active
		? 'bg-pii-ink text-pii-white'
		: 'border border-pii-line bg-pii-white text-pii-ink'}"
>
	<slot />
</button>
