<script lang="ts">
	import { piiTransform } from '$lib/data/trust-centre';
	export let data: typeof piiTransform;
</script>

<div class="flex flex-col items-center gap-8">
	<div class="w-full max-w-[920px] bg-white border border-hg-border-subtle rounded-[18px] shadow-[0px_2px_12px_0px_rgba(0,0,0,0.02)] overflow-hidden">
		<!-- Header: label + static toggle -->
		<div class="flex items-center justify-between px-5 h-[50px] border-b border-hg-border-subtle">
			<span class="font-hg-body font-semibold text-sm text-hg-text-primary">{data.toggleLabel}</span>
			<div class="flex items-center gap-2" aria-hidden="true">
				<span class="font-hg-body text-xs text-hg-text-secondary">{data.toggleState}</span>
				<span class="w-12 h-8 rounded-hg-full bg-hg-blue relative">
					<span class="absolute top-1 right-1 w-6 h-6 rounded-full bg-white"></span>
				</span>
			</div>
		</div>
		<!-- Body: input → output -->
		<div class="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr]">
			<div class="p-10 flex flex-col gap-2">
				<p class="font-hg-body text-sm text-hg-text-primary">
					<span class="font-semibold">{data.inputHeading}</span> {data.inputContext}
				</p>
				<p class="font-hg-body text-sm leading-[1.6] text-hg-text-primary">
					{#each data.inputSegments as seg}{#if seg.pii}<span class="underline decoration-hg-orange">{seg.text}</span>{:else}{seg.text}{/if}{/each}
				</p>
			</div>
			<div class="hidden md:flex items-center justify-center px-2 text-hg-text-tertiary">→</div>
			<div class="p-10 flex flex-col gap-2 bg-hg-orange-50">
				<p class="font-hg-body text-sm font-semibold text-hg-text-primary">{data.outputHeading}</p>
				<p class="font-hg-body text-sm leading-[1.6] text-hg-text-primary">
					{#each data.outputSegments as seg}{#if seg.token}<span class="text-hg-orange font-medium">{seg.token}</span>{:else}{seg.text}{/if}{/each}
				</p>
			</div>
		</div>
		<!-- Footnote -->
		<div class="flex items-center gap-2 px-5 h-[42px] border-t border-hg-border-subtle">
			<span class="text-hg-success-600">✓</span>
			<span class="font-hg-body text-xs text-hg-text-secondary">{data.footnote}</span>
		</div>
	</div>
	<p class="font-hg-body text-sm text-hg-text-tertiary text-center max-w-[572px]">{data.caption}</p>
</div>
