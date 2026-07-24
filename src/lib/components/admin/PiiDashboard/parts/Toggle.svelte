<script lang="ts">
	export let on = false;
	export let disabled = false;
</script>

<button
	type="button"
	{disabled}
	on:click
	aria-pressed={on}
	class="relative h-[21px] w-[38px] rounded-full transition disabled:opacity-60 disabled:cursor-not-allowed {on
		? 'bg-pii-blue'
		: 'bg-pii-control-off'}"
>
	<span
		class="absolute top-[2px] size-[17px] rounded-full bg-white transition-all {on ? 'left-[19px]' : 'left-[2px]'}"
	></span>
</button>
