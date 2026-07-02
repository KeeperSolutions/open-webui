<script lang="ts">
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';

	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';

	import * as trust from '$lib/data/trust-centre';
	import HgIconLock from '$lib/components/icons/HgIconLock.svelte';
	import HgTrustPiiTransform from '$lib/components/hubgate/HgTrustPiiTransform.svelte';
	import HgTrustMarquee from '$lib/components/hubgate/HgTrustMarquee.svelte';
	import HgTrustSectionHeader from '$lib/components/hubgate/HgTrustSectionHeader.svelte';
	import HgTrustPillarCard from '$lib/components/hubgate/HgTrustPillarCard.svelte';
	import HgTrustCertCard from '$lib/components/hubgate/HgTrustCertCard.svelte';
	import HgTrustListItem from '$lib/components/hubgate/HgTrustListItem.svelte';
	import HgTrustSpecTable from '$lib/components/hubgate/HgTrustSpecTable.svelte';
	import HgTrustLogsPanel from '$lib/components/hubgate/HgTrustLogsPanel.svelte';
	import HgTrustDownloadCard from '$lib/components/hubgate/HgTrustDownloadCard.svelte';
	import HgTrustContactCta from '$lib/components/hubgate/HgTrustContactCta.svelte';

	// modelCatalogue's first row carries the table's header badge, not a data row.
	const [modelCatalogueBadgeRow, ...modelCatalogueRows] = trust.modelCatalogue;

	onMount(() => {
		if (get(user)) { goto('/chat'); return; }
	});
</script>

<svelte:head>
	<title>Trust Centre — Hubgate</title>
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

			<main class="flex-1 flex flex-col gap-24 pb-24">
				<!-- Hero -->
				<section class="max-w-[1248px] mx-auto w-full px-4 sm:px-6 pt-12 flex flex-col items-center text-center gap-6">
					<span class="inline-flex items-center gap-1.5 bg-hg-bg-surface border border-hg-text-secondary rounded-[87px] px-4 py-1.5">
						<HgIconLock class="text-hg-success-600" />
						<span class="font-hg-body text-xs text-hg-text-secondary">{trust.hero.pill}</span>
					</span>
					<h1 class="font-hg-heading font-bold text-[clamp(28px,4vw,44px)] leading-[1.2] text-hg-text-primary">
						{trust.hero.headingLead}<span class="text-hg-orange">{trust.hero.headingAccent}</span>
					</h1>
					<p class="font-hg-body text-base leading-[1.6] text-hg-text-secondary max-w-[440px]">{trust.hero.description}</p>
				</section>

				<!-- Signature: PII transform -->
				<section class="max-w-[1248px] mx-auto w-full px-4 sm:px-6">
					<HgTrustPiiTransform data={trust.piiTransform} />
				</section>

				<!-- Marquee (full-bleed) -->
				<HgTrustMarquee pills={trust.marqueePills} />

				<!-- Pillars -->
				<section class="max-w-[1248px] mx-auto w-full px-4 sm:px-6 flex flex-col gap-12">
					<HgTrustSectionHeader {...trust.pillarsSection} />
					<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
						{#each trust.pillars as p}<HgTrustPillarCard icon={p.icon} color={p.color} title={p.title} body={p.body} />{/each}
					</div>
				</section>

				<!-- Hosting & residency -->
				<section class="max-w-[1248px] mx-auto w-full px-4 sm:px-6 grid grid-cols-1 lg:grid-cols-2 gap-8 lg:gap-16 items-start">
					<div class="flex flex-col gap-4">
						<HgTrustSectionHeader {...trust.hostingSection} align="left" />
						<div class="flex flex-col [&>*]:py-3 [&>*]:border-b [&>*]:border-hg-border-subtle [&>*:last-child]:border-b-0">
							{#each trust.hostingList as item}<HgTrustListItem title={item.title} body={item.body} />{/each}
						</div>
					</div>
					<HgTrustSpecTable title="Model catalogue" rows={modelCatalogueRows} headerBadge={modelCatalogueBadgeRow.badge} />
				</section>

				<!-- Encryption band -->
				<section class="w-full">
					<div class="max-w-[1248px] mx-auto w-full px-4 sm:px-6 py-16 grid grid-cols-1 lg:grid-cols-2 gap-8 lg:gap-16 items-start">
						<HgTrustSpecTable title="Data handling at a glance" rows={trust.dataHandlingRows} />
						<div class="flex flex-col gap-4">
							<HgTrustSectionHeader {...trust.encryptionSection} align="left" />
							<div class="flex flex-col [&>*]:py-3 [&>*]:border-b [&>*]:border-hg-border-subtle [&>*:last-child]:border-b-0">
								{#each trust.encryptionList as item}<HgTrustListItem title={item.title} body={item.body} />{/each}
							</div>
						</div>
					</div>
				</section>

				<!-- Certifications -->
				<section class="max-w-[1248px] mx-auto w-full px-4 sm:px-6 flex flex-col gap-12">
					<HgTrustSectionHeader {...trust.certsSection} />
					<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
						{#each trust.certifications as c}<HgTrustCertCard name={c.name} badge={c.badge} body={c.body} bodyAccent={c.bodyAccent} />{/each}
					</div>
				</section>

				<!-- Audit signature -->
				<section class="max-w-[1248px] mx-auto w-full px-4 sm:px-6">
					<div
						class="bg-white border border-hg-border-subtle rounded-[24px] overflow-hidden shadow-[0px_30px_60px_-24px_rgba(240,201,150,0.07),0px_6px_16px_0px_rgba(108,108,109,0.06)] grid grid-cols-1 lg:grid-cols-2 items-stretch"
					>
						<div class="p-8 flex flex-col gap-4">
							<HgTrustSectionHeader {...trust.auditSection} align="left" />
							<div class="flex flex-col [&>*]:py-3">
								{#each trust.auditList as item}<HgTrustListItem title={item.title} body={item.body} />{/each}
							</div>
						</div>
						<HgTrustLogsPanel />
					</div>
				</section>

				<!-- Know Your Agent band -->
				<section class="w-full">
					<div class="max-w-[1248px] mx-auto w-full px-4 sm:px-6 py-12 flex flex-col gap-6">
						<HgTrustSectionHeader {...trust.kyaSection} />
						<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
							{#each trust.kyaCards as k}<HgTrustPillarCard icon={k.icon} color={k.color} title={k.title} body={k.body} />{/each}
						</div>
					</div>
				</section>

				<!-- Downloads -->
				<section class="max-w-[1248px] mx-auto w-full px-4 sm:px-6 flex flex-col gap-6">
					<HgTrustSectionHeader {...trust.downloadsSection} descNarrow />
					<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
						{#each trust.downloads as d}<HgTrustDownloadCard icon={d.icon} title={d.title} meta={d.meta} href={d.href} />{/each}
					</div>
				</section>

				<!-- Contact CTA -->
				<section class="max-w-[1248px] mx-auto w-full px-4 sm:px-6">
					<HgTrustContactCta title={trust.contactCta.title} body={trust.contactCta.body} buttonLabel={trust.contactCta.buttonLabel} buttonHref={trust.contactCta.buttonHref} contacts={trust.contacts} />
				</section>
			</main>

			<HgLandingFooter />
		</div>
	</div>
{/if}
