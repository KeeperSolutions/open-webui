<script lang="ts">
	export let eyebrow: string;
	export let title: string;
	export let description: string;
</script>

<div class="flex flex-col items-center text-center gap-4 max-w-[652px] mx-auto">
	<span class="font-hg-body font-bold text-xs uppercase tracking-[0.48px] text-hg-orange">{eyebrow}</span>
	<h2 class="font-hg-heading font-bold text-[clamp(24px,3vw,28px)] leading-[1.2] text-hg-text-primary">{title}</h2>
	<p class="font-hg-body text-base leading-[1.6] text-hg-text-secondary max-w-[540px]">{description}</p>
</div>
