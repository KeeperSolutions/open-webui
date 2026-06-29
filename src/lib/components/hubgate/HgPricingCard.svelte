<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import HgIconInfo from '$lib/components/icons/HgIconInfo.svelte';
	import type { PricingPlan } from '$lib/data/pricing-plans';

	export let plan: PricingPlan;

	const dispatch = createEventDispatcher();
</script>

<div
	class="group relative bg-hg-bg-surface border border-hg-border rounded-[24px] shadow-[0px_4px_24px_-8px_rgba(0,0,0,0.06)] hover:border-hg-border-focus hover:shadow-[0px_0px_24px_-11px_rgba(33,45,72,0.4)] overflow-hidden flex flex-col w-full max-w-[300px] mx-auto md:max-w-none md:mx-0 transition-[border-color,box-shadow] duration-200"
>
	<!-- Header -->
	<div class="relative p-4 lg:px-3 flex flex-col gap-2">
		<div class="flex flex-col gap-1 min-h-[52px]">
			<span class="font-hg-body font-semibold text-sm text-hg-text-secondary leading-[1.4]">
				{plan.name}
			</span>
			<span class="font-hg-body text-xs text-hg-text-tertiary leading-[1.4]" class:pr-24={plan.isMostPopular}
				>{plan.tagline}</span
			>
		</div>

		{#if plan.isMostPopular}
			<div
				class="absolute top-4 right-4 inline-flex items-center px-2 py-1 rounded-hg-full bg-hg-blue"
			>
				<span class="font-hg-body text-xs text-white leading-[1.4] whitespace-nowrap">
					Most Popular
				</span>
			</div>
		{/if}

		<div class="flex flex-col gap-0">
			<div class="flex items-center gap-1">
				<span class="font-hg-body text-sm text-hg-text-tertiary leading-[1.4]">{plan.currency}</span
				>
				<span class="font-hg-heading font-bold text-[28px] text-hg-text-primary leading-[1.2]">
					{plan.price}
				</span>
				{#if plan.priceSuffix}
					<span class="font-hg-body text-sm text-hg-text-tertiary leading-[1.4]">
						{plan.priceSuffix}
					</span>
				{/if}
			</div>
			{#if plan.seatPrice}
				<span class="font-hg-body text-xs text-hg-text-tertiary leading-[1.4]">
					+ {plan.currency}{plan.seatPrice} / seat / month
				</span>
			{/if}
		</div>
		{#if plan.creditsHighlight}
			<div
				class="inline-flex items-center gap-3 self-start px-3 py-2 rounded-hg-md bg-hg-bg-muted border border-hg-border"
			>
				<div class="flex items-baseline gap-1 whitespace-nowrap">
					<span class="font-hg-body text-sm font-semibold text-hg-orange leading-[1.4]"
						>{plan.creditsHighlight}</span
					>
					<span class="font-hg-body text-xs text-hg-text-secondary leading-[1.4]"
						>{plan.creditsLabel}</span
					>
				</div>
				<HgIconInfo class="w-4 h-4 shrink-0 text-hg-text-tertiary" />
			</div>
		{/if}
	</div>

	<!-- Features + CTA -->
	<div class="border-t border-hg-border-subtle flex-1 flex flex-col justify-between p-4 lg:px-3 pt-[17px]">
		<ul class="flex flex-col gap-2 mb-4">
			{#each plan.features as feature}
				<li class="flex items-center gap-1">
					<Check className="w-4 h-4 shrink-0 text-hg-blue" strokeWidth="2" />
					<span class="font-hg-body text-xs text-hg-text-secondary leading-[1.4]">{feature}</span>
				</li>
			{/each}
		</ul>

		<div class="flex flex-col items-center gap-1">
			<button
				type="button"
				class="w-full h-8 px-4 rounded-hg-full bg-hg-text-primary text-white font-hg-body text-xs flex items-center justify-center transition-colors duration-200 group-hover:bg-hg-blue group-active:bg-hg-blue/80"
				on:click={() => dispatch('cta')}
			>
				{plan.ctaLabel}
			</button>
			{#if plan.note}
				<span class="font-hg-body text-xs text-hg-text-tertiary text-center leading-[1.4]">
					{plan.note}
				</span>
			{/if}
		</div>
	</div>
</div>
