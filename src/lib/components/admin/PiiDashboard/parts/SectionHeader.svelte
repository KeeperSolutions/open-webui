<script lang="ts">
	export let num: string;
	export let title: string;
	export let subtitle: string;
</script>

<div class="flex items-center gap-3 font-['Inter']">
	<span class="rounded-full bg-pii-blue px-2.5 py-[3px] text-[11px] font-bold leading-[1.55] text-pii-white">
		{num}
	</span>
	<span class="text-[15.5px] font-bold leading-[1.55] text-pii-ink whitespace-nowrap">{title}</span>
	<span class="text-[12px] leading-[1.55] text-pii-muted whitespace-nowrap">{subtitle}</span>
</div>
