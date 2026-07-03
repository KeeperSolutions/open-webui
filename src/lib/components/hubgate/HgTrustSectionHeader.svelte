<script lang="ts">
	export let eyebrow: string;
	export let title: string;
	export let description: string;
	export let align: 'center' | 'left' = 'center';
	// Narrower centered description (matches Figma sections that wrap tightly)
	export let descNarrow = false;

	$: descMaxW = align === 'left' ? 'max-w-[488px]' : descNarrow ? 'max-w-[368px]' : 'max-w-[540px]';
</script>

<div
	class="flex flex-col gap-4 {align === 'left'
		? 'items-start text-left'
		: 'items-center text-center max-w-[652px] mx-auto'}"
>
	<span class="font-hg-body font-bold text-xs uppercase tracking-[0.48px] text-hg-orange">{eyebrow}</span>
	<h2 class="font-hg-heading font-bold text-[clamp(24px,3vw,28px)] leading-[1.2] text-hg-text-primary whitespace-pre-line">
		{title}
	</h2>
	<p class="font-hg-body text-base leading-[1.6] text-hg-text-secondary {descMaxW}">
		{description}
	</p>
</div>
