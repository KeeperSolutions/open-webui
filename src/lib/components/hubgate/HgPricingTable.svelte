<script lang="ts">
	import type { ModelClass } from '$lib/apis/model-classes';

	export let modelClasses: ModelClass[] = [];
	export let loading = false;

	$: maxCreditBurn = modelClasses.length
		? Math.max(...modelClasses.map((m) => m.credit_burn))
		: 1;

	const blurbs = [
		{
			title: 'Mix models freely',
			body: "Burn rates track each model's real compute cost, so switching models never changes your bill logic — only how far credits stretch."
		},
		{
			title: 'Monthly reset',
			body: "Credits refresh each month and don't roll over. Top-ups are available any time and never expire mid-cycle."
		},
		{
			title: 'Credits are pooled on Business',
			body: "Your whole team draws from one balance — occasional users stop costing full seats, heavy users aren't capped per person."
		}
	];
</script>

<div class="flex flex-col gap-6">
	<!-- Heading block -->
	<div class="flex flex-col gap-3">
		<div class="flex flex-col gap-2">
			<span class="font-hg-body font-bold text-xs text-hg-orange uppercase tracking-[0.48px]"
				>no hidden maths</span
			>
			<h2 class="font-hg-heading font-bold text-[28px] text-hg-text-primary leading-[1.2]">
				Exactly what your plan buys, model by model
			</h2>
		</div>
		<p class="font-hg-body text-base text-hg-text-secondary leading-[1.6] max-w-[800px]">
			Every model draws down credits at a published rate. The table shows how far each plan goes if
			you used only that model all month — most people mix, so real mileage sits in between.
		</p>
		<div
			class="self-start inline-flex items-center bg-hg-bg-surface border border-hg-text-secondary rounded-[87px] px-4 py-1.5"
		>
			<span class="font-hg-body text-xs text-hg-text-secondary"
				>A standard message =&nbsp;<span
					class="font-hg-body font-semibold text-sm text-hg-orange">1,500</span
				>&nbsp;tokens in +&nbsp;<span class="font-hg-body font-semibold text-sm text-hg-orange"
					>500</span
				>&nbsp;tokens out</span
			>
		</div>
	</div>

	<!-- Table -->
	<div
		class="bg-hg-bg-surface border border-[#f5f5f4] rounded-[16px] overflow-hidden"
	>
		<table class="w-full border-collapse">
			<thead>
				<tr class="bg-[#fffdfc] border-b border-[#f5f5f4]">
					<th
						class="text-left px-3 md:px-5 py-3 font-hg-body font-bold text-xs text-hg-text-tertiary uppercase tracking-[0.48px] w-1/2 md:w-auto md:min-w-[180px]"
					>
						Model class
					</th>
					<th
						class="text-right px-3 md:px-5 py-3 font-hg-body font-bold text-xs text-hg-text-tertiary uppercase tracking-[0.48px] w-1/2 md:w-auto md:max-w-[240px]"
					>
						Credit burn / message
					</th>
					<th
						class="hidden md:table-cell text-center px-5 py-3 font-hg-body font-bold text-xs text-hg-text-tertiary uppercase tracking-[0.48px] max-w-[220px]"
					>
						Pro · 1,300 cr
					</th>
					<th
						class="hidden md:table-cell text-center px-5 py-3 font-hg-body font-bold text-xs text-hg-text-tertiary uppercase tracking-[0.48px] max-w-[220px]"
					>
						Premium · 3,800 cr
					</th>
					<th
						class="hidden md:table-cell text-center px-5 py-3 font-hg-body font-bold text-xs text-hg-text-tertiary uppercase tracking-[0.48px] max-w-[220px]"
					>
						Business · 1,000 cr / seat
					</th>
				</tr>
			</thead>
			<tbody>
				{#if loading}
					{#each Array(5) as _}
						<tr class="border-b border-[#f5f5f4]">
							<td class="px-3 md:px-5 py-4">
								<div class="flex flex-col gap-2">
									<div class="h-[18px] w-24 bg-hg-bg-muted animate-pulse rounded" />
									<div class="h-[14px] w-40 bg-hg-bg-muted animate-pulse rounded" />
								</div>
							</td>
							<td class="px-3 md:px-5 py-4">
								<div class="flex flex-col gap-2 items-end">
									<div class="h-[18px] w-10 bg-hg-bg-muted animate-pulse rounded" />
									<div class="hidden md:block h-2 w-full bg-hg-bg-muted animate-pulse rounded-full" />
								</div>
							</td>
							<td class="hidden md:table-cell px-5 py-4">
								<div class="flex flex-col gap-2 items-center">
									<div class="h-[18px] w-16 bg-hg-bg-muted animate-pulse rounded" />
									<div class="h-[14px] w-20 bg-hg-bg-muted animate-pulse rounded" />
								</div>
							</td>
							<td class="hidden md:table-cell px-5 py-4">
								<div class="flex flex-col gap-2 items-center">
									<div class="h-[18px] w-16 bg-hg-bg-muted animate-pulse rounded" />
									<div class="h-[14px] w-20 bg-hg-bg-muted animate-pulse rounded" />
								</div>
							</td>
							<td class="hidden md:table-cell px-5 py-4">
								<div class="flex flex-col gap-2 items-center">
									<div class="h-[18px] w-16 bg-hg-bg-muted animate-pulse rounded" />
									<div class="h-[14px] w-20 bg-hg-bg-muted animate-pulse rounded" />
								</div>
							</td>
						</tr>
					{/each}
				{:else}
					{#each modelClasses as mc, i}
						<tr class={i < modelClasses.length - 1 ? 'border-b border-[#f5f5f4]' : ''}>
							<!-- Model class name + models + progress bar on mobile -->
							<td class="px-3 md:px-5 py-3">
								<div class="flex flex-col gap-1">
									<span
										class="font-hg-body font-semibold text-base text-hg-text-primary leading-[1.4]"
										>{mc.name}</span
									>
									{#if mc.models && mc.models.length > 0}
										<span class="font-hg-body text-xs text-hg-text-tertiary leading-[1.4]"
											>{mc.models.join(' · ')}</span
										>
									{/if}
									<div class="md:hidden w-full bg-[#fff7ed] h-2 rounded-full overflow-hidden mt-1">
										<div
											class="bg-hg-orange h-2 rounded-full"
											style="width: {Math.min((mc.credit_burn / maxCreditBurn) * 100, 100)}%"
										/>
									</div>
								</div>
							</td>
							<!-- Credit burn + progress bar (progress on lg only) -->
							<td class="px-3 md:px-5 py-2">
								<div class="flex flex-col gap-1 items-end">
									<span
										class="font-hg-body font-semibold text-sm text-hg-text-primary leading-[1.4] text-right"
										>{mc.credit_burn}</span
									>
									<div class="hidden md:block w-full bg-[#fff7ed] h-2 rounded-full overflow-hidden">
										<div
											class="bg-hg-orange h-2 rounded-full"
											style="width: {Math.min((mc.credit_burn / maxCreditBurn) * 100, 100)}%"
										/>
									</div>
								</div>
							</td>
							<!-- Pro -->
							<td class="hidden md:table-cell px-5 py-3 text-center">
								{#if mc.msgs_pro}
									<div class="flex flex-col gap-1 items-center">
										<span
											class="font-hg-body font-semibold text-sm text-hg-text-primary leading-[1.4]"
											>~{mc.msgs_pro}</span
										>
										<span class="font-hg-body text-xs text-hg-text-tertiary leading-[1.4]"
											>msgs / month</span
										>
									</div>
								{:else}
									<span class="font-hg-body text-sm text-hg-text-tertiary">—</span>
								{/if}
							</td>
							<!-- Premium -->
							<td class="hidden md:table-cell px-5 py-3 text-center">
								{#if mc.msgs_premium}
									<div class="flex flex-col gap-1 items-center">
										<span
											class="font-hg-body font-semibold text-sm text-hg-text-primary leading-[1.4]"
											>~{mc.msgs_premium}</span
										>
										<span class="font-hg-body text-xs text-hg-text-tertiary leading-[1.4]"
											>msgs / month</span
										>
									</div>
								{:else}
									<span class="font-hg-body text-sm text-hg-text-tertiary">—</span>
								{/if}
							</td>
							<!-- Business -->
							<td class="hidden md:table-cell px-5 py-3 text-center">
								{#if mc.msgs_business}
									<div class="flex flex-col gap-1 items-center">
										<span
											class="font-hg-body font-semibold text-sm text-hg-text-primary leading-[1.4]"
											>~{mc.msgs_business}</span
										>
										<span class="font-hg-body text-xs text-hg-text-tertiary leading-[1.4]"
											>msgs / seat</span
										>
									</div>
								{:else}
									<span class="font-hg-body text-sm text-hg-text-tertiary">—</span>
								{/if}
							</td>
						</tr>
					{/each}
				{/if}
			</tbody>
		</table>
	</div>

	<!-- Footer blurbs -->
	<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8 lg:gap-16">
		{#each blurbs as blurb}
			<div class="flex flex-col gap-1">
				<span class="font-hg-heading font-medium text-[18px] text-hg-text-primary leading-[1.4]"
					>{blurb.title}</span
				>
				<p class="font-hg-body text-base text-hg-text-secondary leading-[1.6]">{blurb.body}</p>
			</div>
		{/each}
	</div>
</div>
