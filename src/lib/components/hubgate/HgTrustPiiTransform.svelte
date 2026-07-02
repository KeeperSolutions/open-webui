<script lang="ts">
	import { piiTransform } from '$lib/data/trust-centre';
	import HgIconShieldPixel from '$lib/components/icons/HgIconShieldPixel.svelte';
	import HgIconTogglePixel from '$lib/components/icons/HgIconTogglePixel.svelte';
	export let data: typeof piiTransform;

	// Output token pill colors, matching the Figma PIIElement styles
	const tokenClasses: Record<string, string> = {
		blue: 'bg-[#dbeafe] text-hg-blue',
		green: 'bg-[#dcfce7] text-hg-success-600',
		amber: 'bg-hg-warning-100 text-hg-warning-600'
	};
</script>

<div class="flex flex-col items-center gap-4">
	<div
		class="w-full max-w-[920px] bg-white border border-hg-border-subtle rounded-[24px] overflow-hidden shadow-[0px_30px_60px_-24px_rgba(240,201,150,0.07),0px_6px_16px_0px_rgba(108,108,109,0.06)]"
	>
		<!-- Header: shield + label · state + static pixel toggle -->
		<div
			class="flex items-center justify-between px-5 py-2 border-b border-hg-border-subtle bg-gradient-to-b from-white to-[#fcfcfe]"
		>
			<div class="flex items-center gap-2">
				<HgIconShieldPixel class="text-hg-blue w-[15px] h-[18px]" />
				<span class="font-hg-heading font-medium text-lg text-hg-text-primary">{data.toggleLabel}</span>
			</div>
			<div class="flex items-center gap-3" aria-hidden="true">
				<span class="font-hg-body font-semibold text-base text-hg-text-primary">{data.toggleState}</span>
				<HgIconTogglePixel class="text-hg-blue w-[45px] h-[19px]" />
			</div>
		</div>

		<!-- Body: input → output -->
		<div class="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr]">
			<!-- What you type -->
			<div class="p-10 flex flex-col gap-4">
				<p class="font-hg-heading font-medium text-lg">
					<span class="text-hg-text-tertiary">{data.inputHeading}</span>
					<span class="text-hg-text-primary">{data.inputContext}</span>
				</p>
				<p class="flex flex-wrap items-baseline gap-x-1 gap-y-2 font-hg-body text-sm text-hg-text-primary">
					{#each data.inputSegments as seg}{#if seg.pii}<span
								class="font-bold border-b-2 border-dashed border-hg-orange">{seg.text}</span
							>{:else}<span>{seg.text}</span>{/if}{/each}
				</p>
			</div>

			<!-- Arrow -->
			<div
				class="hidden md:flex items-center justify-center px-2 text-hg-text-tertiary"
				aria-hidden="true"
			>
				→
			</div>

			<!-- What the model receives -->
			<div class="p-10 flex flex-col gap-4 bg-hg-info-bg">
				<p class="font-hg-heading font-medium text-lg text-hg-text-tertiary">{data.outputHeading}</p>
				<p class="flex flex-wrap items-baseline gap-x-1 gap-y-2 font-hg-body text-sm text-hg-text-primary">
					{#each data.outputSegments as seg}{#if seg.token}<span
								class="font-bold px-1 rounded-[4px] {tokenClasses[seg.variant ?? 'blue']}"
								>{seg.token}</span
							>{:else}<span>{seg.text}</span>{/if}{/each}
				</p>
			</div>
		</div>

		<!-- Footnote -->
		<div
			class="flex items-center gap-2 px-5 py-3 border-t border-hg-border-subtle bg-gradient-to-b from-white to-[#fcfcfe]"
		>
			<span class="text-hg-success-600" aria-hidden="true">✓</span>
			<span class="font-hg-body text-xs text-hg-text-tertiary">{data.footnote}</span>
		</div>
	</div>
	<p class="font-hg-body text-sm text-hg-text-tertiary text-center max-w-[572px]">
		{data.caption.before}<span class="text-hg-text-primary">{data.caption.emphasis}</span>{data.caption.after}
	</p>
</div>
