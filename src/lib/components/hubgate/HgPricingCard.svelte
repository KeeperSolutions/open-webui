<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import type { PricingPlan } from '$lib/data/pricing-plans';

	export let plan: PricingPlan;

	const dispatch = createEventDispatcher();
</script>

<div
	class="group relative bg-hg-bg-surface border border-hg-border rounded-[24px] shadow-[0px_4px_24px_-8px_rgba(0,0,0,0.06)] hover:border-hg-border-focus hover:shadow-[0px_0px_24px_-11px_rgba(33,45,72,0.4)] overflow-hidden flex flex-col w-full max-w-[300px] transition-[border-color,box-shadow] duration-200"
>
	<!-- Header -->
	<div class="relative p-4 flex flex-col gap-2">
		<div class="flex flex-col gap-1 pr-24">
			<span class="font-hg-body font-semibold text-sm text-hg-text-secondary leading-[1.4]">
				{plan.name}
			</span>
			<span class="font-hg-body text-xs text-hg-text-tertiary leading-[1.4]">{plan.tagline}</span>
		</div>

		{#if plan.isMostPopular}
			<div
				class="absolute top-4 right-4 inline-flex items-center px-2 py-1 rounded-hg-full bg-[#eff6ff] border border-[#bfdbfe]"
			>
				<span class="font-hg-body text-xs text-[#1e40af] leading-[1.4] whitespace-nowrap">
					Most Popular
				</span>
			</div>
		{/if}

		<div class="flex items-center gap-1">
			<span class="font-hg-body text-sm text-hg-text-tertiary leading-[1.4]">{plan.currency}</span>
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
			<span class="font-hg-body text-sm text-hg-text-tertiary leading-[0.1]">
				{plan.seatPrice}
			</span>
		{/if}
	</div>

	<!-- Features + CTA -->
	<div class="border-t border-hg-border-subtle flex-1 flex flex-col justify-between p-4 pt-[17px]">
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
				class="w-full h-8 px-4 rounded-hg-full border border-hg-border bg-hg-bg-surface text-hg-text-secondary group-hover:bg-hg-blue group-hover:border-hg-blue group-hover:text-white font-hg-body text-xs transition-colors duration-200"
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
