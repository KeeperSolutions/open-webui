<script lang="ts">
	export let color: 'blue' | 'green' | 'amber' = 'blue';
	export let name: string;
	export let value: string;
	export let percent: number;
	/** Shown on hover when the label alone does not identify the row — e.g. a
	 *  display name whose account is the email behind it. */
	export let detail: string | undefined = undefined;

	const fillCls: Record<typeof color, string> = {
		blue: 'bg-pii-blue',
		green: 'bg-pii-green',
		amber: 'bg-pii-amber-mid'
	};
	$: width = `${Math.max(0, Math.min(100, percent))}%`;
</script>

<div class="flex w-full items-center gap-2.5 font-['Inter']">
	<!-- Capped by share of the row as well as by pixels: on a narrow viewport a
	     fixed 170px label leaves the bar nothing to draw in. -->
	<span
		title={detail ?? name}
		class="w-[170px] max-w-[45%] shrink-0 truncate text-[12.5px] leading-[1.55] text-pii-muted"
	>
		{name}
	</span>
	<div class="h-3 flex-1 overflow-hidden rounded-full bg-pii-track">
		<div class="h-full rounded-full {fillCls[color]}" style="width: {width}"></div>
	</div>
	<span class="w-[70px] shrink-0 text-right text-[12.5px] font-bold leading-[1.55] text-pii-ink">{value}</span>
</div>
