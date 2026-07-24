<script lang="ts">
	export let color: 'blue' | 'green' | 'amber' = 'blue';
	export let name: string;
	export let value: string;
	export let percent: number;

	const fillCls: Record<typeof color, string> = {
		blue: 'bg-pii-blue',
		green: 'bg-pii-green',
		amber: 'bg-pii-amber-mid'
	};
	$: width = `${Math.max(0, Math.min(100, percent))}%`;
</script>

<div class="flex w-full items-center gap-2.5 font-['Inter']">
	<span class="w-[170px] shrink-0 truncate text-[12.5px] leading-[1.55] text-pii-muted">{name}</span>
	<div class="h-3 flex-1 overflow-hidden rounded-full bg-pii-track">
		<div class="h-full rounded-full {fillCls[color]}" style="width: {width}"></div>
	</div>
	<span class="w-[70px] shrink-0 text-right text-[12.5px] font-bold leading-[1.55] text-pii-ink">{value}</span>
</div>
