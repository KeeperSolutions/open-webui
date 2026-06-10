<script lang="ts">
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import { handleAuthSuccess } from '$lib/utils/auth';
	import { plans, type PricingPlan } from '$lib/data/pricing-plans';

	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';
	import HgPricingCard from '$lib/components/hubgate/HgPricingCard.svelte';
	import HgAuthModal from '$lib/components/hubgate/HgAuthModal.svelte';
	import HgIconLock from '$lib/components/icons/HgIconLock.svelte';

	let showModal = false;

	const openModal = (plan: PricingPlan) => {
		if (plan.postLoginRedirect) {
			localStorage.setItem('postLoginRedirect', plan.postLoginRedirect);
		} else {
			localStorage.removeItem('postLoginRedirect');
		}
		showModal = true;
	};

	$: if ($user) goto('/chat');

	const onSuccess = async (e: CustomEvent) => {
		showModal = false;
		await handleAuthSuccess(e.detail);
	};
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

				<!-- Pricing cards: 1-col mobile, 2-col tablet, all-in-one-row desktop -->
				<div class="px-6 pb-4 w-full flex justify-center">
					<div
						id="pricing-cards"
						class="grid gap-4 grid-cols-1 sm:grid-cols-2"
						style="--cols: {plans.length}"
					>
						{#each plans as plan, i}
							<div
								class="flex justify-center"
								class:lone-last-card={plans.length % 2 !== 0 && i === plans.length - 1}
							>
								<HgPricingCard {plan} on:cta={() => openModal(plan)} />
							</div>
						{/each}
					</div>
				</div>

				<p
					class="px-6 pb-16 text-center font-hg-body text-xs font-normal leading-[1.4] text-hg-text-secondary max-w-[440px]"
				>
					Credits reset monthly and do not roll over. Queries beyond your allowance require a
					top-up.
				</p>
			</main>

			<HgLandingFooter />
		</div>
	</div>

	<HgAuthModal bind:open={showModal} on:success={onSuccess} />
{/if}

<style>
	/* Tablet (2-col): the lone last card of an odd-count grid spans both columns and centers. */
	@media (min-width: 640px) and (max-width: 1023px) {
		:global(.lone-last-card) {
			grid-column: span 2;
		}
	}

	/* Laptop and wider: every plan in a single centered row, each card 300px. */
	@media (min-width: 1024px) {
		:global(#pricing-cards) {
			grid-template-columns: repeat(var(--cols), 300px);
			justify-content: center;
		}
	}
</style>
