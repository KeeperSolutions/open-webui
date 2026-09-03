<script lang="ts">
	export let kind: 'ok' | 'warn' | 'bad' | 'info' = 'info';
	export let dot = false;
	export let size: 'sm' | 'lg' = 'sm';

	const bg: Record<typeof kind, string> = {
		ok: 'bg-pii-green-soft',
		warn: 'bg-pii-amber-soft',
		bad: 'bg-pii-red-soft',
		info: 'bg-pii-blue-soft'
	};
	const fg: Record<typeof kind, string> = {
		ok: 'text-pii-green',
		warn: 'text-pii-amber',
		bad: 'text-pii-red',
		info: 'text-pii-blue'
	};
	$: sizeCls = size === 'lg' ? 'px-4 py-[7px] text-[14px]' : 'px-3 py-1 text-[11.5px]';
</script>

<span
	class="inline-flex items-center gap-1.5 rounded-full font-bold leading-[1.55] whitespace-nowrap {bg[
		kind
	]} {fg[kind]} {sizeCls}"
>
	{#if dot}
		<span class="size-[7px] shrink-0 rounded-full bg-current"></span>
	{/if}
	<slot />
</span>
