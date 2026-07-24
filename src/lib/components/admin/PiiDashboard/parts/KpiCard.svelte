<script lang="ts">
	import Pill from './Pill.svelte';

	export let type: 'numeric' | 'text' | 'status' = 'numeric';
	export let label: string;
	export let value: string;
	export let delta: string | undefined = undefined;
	export let pill: { kind: 'ok' | 'warn' | 'bad' | 'info'; label: string } | undefined = undefined;
</script>

<div
	class="flex flex-1 flex-col items-start gap-1.5 rounded-2xl border border-pii-line bg-pii-white p-5 drop-shadow-[0px_1px_1.5px_rgba(16,24,40,0.04)] font-['Inter']"
>
	<span class="text-[12px] font-semibold leading-[1.55] text-pii-muted whitespace-nowrap">{label}</span>

	{#if type === 'status'}
		<div class="pt-1 pb-0.5">
			<Pill kind="ok" size="lg" dot>{value}</Pill>
		</div>
	{:else if type === 'text'}
		<span class="text-[20px] font-extrabold leading-[1.55] text-pii-ink whitespace-nowrap">{value}</span>
	{:else}
		<span class="text-[27px] font-extrabold leading-[1.55] text-pii-ink whitespace-nowrap">{value}</span>
	{/if}

	{#if delta || pill}
		<div class="flex w-full flex-wrap items-center gap-x-1.5">
			{#if pill}
				<Pill kind={pill.kind}>{pill.label}</Pill>
			{/if}
			{#if delta}
				<span class="flex-1 text-[12px] leading-[1.55] text-pii-muted">{delta}</span>
			{/if}
		</div>
	{/if}
</div>
