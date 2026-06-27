<script lang="ts">
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import { handleAuthSuccess } from '$lib/utils/auth';
	import { plans, type PricingPlan } from '$lib/data/pricing-plans';
	import { getModelClasses, type ModelClass } from '$lib/apis/model-classes';

	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';
	import HgPricingCard from '$lib/components/hubgate/HgPricingCard.svelte';
	import HgPricingTable from '$lib/components/hubgate/HgPricingTable.svelte';
	import HgAuthModal from '$lib/components/hubgate/HgAuthModal.svelte';
	import HgIconLock from '$lib/components/icons/HgIconLock.svelte';

	let showModal = false;
	let modelClasses: ModelClass[] = [];
	let tableLoading = true;

	const openModal = (plan: PricingPlan) => {
		if (plan.postLoginRedirect) {
			localStorage.setItem('postLoginRedirect', plan.postLoginRedirect);
		} else {
			localStorage.removeItem('postLoginRedirect');
		}
		showModal = true;
	};

	const onSuccess = async (e: CustomEvent) => {
		showModal = false;
		await handleAuthSuccess(e.detail);
	};

	onMount(async () => {
		if (get(user)) { goto('/chat'); return; }

		try {
			modelClasses = await getModelClasses();
		} catch {
			modelClasses = [];
		} finally {
			tableLoading = false;
		}
	});
</script>

<svelte:head>
	<title>Pricing — Hubgate</title>
</svelte:head>

{#if !$user}
	<div class="relative min-h-screen flex flex-col font-hg-body">
		<img
			src="/hubgate/hubgate-pixel-pattern.svg"
			alt=""
			aria-hidden="true"
			class="pointer-events-none"
			style="position:fixed;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:0"
		/>

		<div class="relative flex flex-col flex-1" style="z-index:1">
			<HgLandingHeader />

			<main class="flex-1 flex flex-col items-center justify-center">
				<!-- Hero -->
				<div class="flex flex-col items-center text-center px-8 pt-8 pb-0">
					<div class="mb-6">
						<img src="/hubgate/hubgate-logo.svg" alt="Hubgate" width="120" height="24" />
					</div>

					<div
						class="mb-6 inline-flex items-center gap-1.5 bg-hg-bg-surface border border-hg-text-secondary rounded-[87px] px-4 py-1.5"
					>
						<HgIconLock class="text-hg-success-600" />
						<span class="font-hg-body text-xs text-hg-text-secondary"
							>No data used to train AI models</span
						>
					</div>

					<h1
						class="font-hg-heading font-bold text-[clamp(28px,4vw,44px)] leading-[1.2] text-hg-text-primary mb-4"
					>
						Start free. <span class="text-hg-orange">Scale securely.</span>
					</h1>

					<p
						class="font-hg-body text-base text-hg-text-secondary leading-[1.6] mb-10 max-w-[440px]"
					>
						Start for free. Scale your AI usage securely with built-in governance and automatic PII
						masking.
					</p>
				</div>

				<!-- Pricing cards + Enterprise row in one grid -->
				<div class="px-4 pb-4 w-full max-w-[1280px] mx-auto">
					<div
						id="pricing-cards"
						class="grid gap-4 lg:gap-3 grid-cols-1 md:grid-cols-2 lg:grid-cols-4"
					>
						{#each plans as plan}
							<HgPricingCard {plan} on:cta={() => openModal(plan)} />
						{/each}

						<!-- Enterprise: always spans all columns -->
						<div class="col-span-1 md:col-span-2 lg:col-span-4 flex items-center justify-between gap-4 bg-hg-bg-surface border border-hg-border rounded-[24px] px-6 py-4 max-w-[300px] mx-auto md:max-w-none md:mx-0">
							<div class="flex flex-col gap-0.5">
								<span class="font-hg-body font-semibold text-sm text-hg-text-secondary leading-[1.4]">Enterprise</span>
								<span class="font-hg-body text-xs text-hg-text-tertiary leading-[1.4]">EU data residency · DPA · custom agents & RAG · volume discounts</span>
							</div>
							<a
								href="mailto:contact@hubgate.io"
								class="shrink-0 h-8 px-4 inline-flex items-center rounded-hg-full border border-hg-border bg-hg-bg-surface text-hg-text-secondary hover:bg-hg-blue hover:border-hg-blue hover:text-white font-hg-body text-xs transition-colors duration-200"
							>
								Contact Us
							</a>
						</div>
					</div>
				</div>

				<p
					class="px-6 pb-10 text-center font-hg-body text-base font-normal leading-[1.6] text-hg-text-tertiary max-w-[440px]"
				>
					Credits reset monthly and do not roll over. Queries beyond your allowance require a
					top-up.
				</p>

				{#if tableLoading || modelClasses.length > 0}
					<div class="px-4 pb-16 w-full max-w-[1280px] mx-auto">
						<HgPricingTable {modelClasses} loading={tableLoading} />
					</div>
				{/if}
			</main>

			<HgLandingFooter />
		</div>
	</div>

	<HgAuthModal bind:open={showModal} on:success={onSuccess} />
{/if}
