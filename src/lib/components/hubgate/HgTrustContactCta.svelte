<script lang="ts">
	import type { TrustContact } from '$lib/data/trust-centre';
	export let title: string;
	export let body: string;
	export let contacts: TrustContact[];

	let copied: string | null = null;
	let timer: ReturnType<typeof setTimeout>;

	async function copyEmail(email: string) {
		try {
			await navigator.clipboard.writeText(email);
			copied = email;
			clearTimeout(timer);
			timer = setTimeout(() => (copied = null), 1500);
		} catch {
			// Clipboard API unavailable — silently ignore
		}
	}
</script>

<div class="rounded-[24px] bg-[#172554] p-8 sm:p-10 grid grid-cols-1 md:grid-cols-2 gap-4 items-center">
	<div class="flex flex-col gap-4 items-start">
		<h2 class="font-hg-heading font-bold text-[clamp(24px,3vw,28px)] leading-[1.2] text-[#fafaf9] max-w-[488px]">
			{title}
		</h2>
		<p class="font-hg-body text-base leading-[1.6] text-[#fafaf9] max-w-[488px]">{body}</p>
	</div>
	<div class="flex flex-col gap-4">
		{#each contacts as c}
			<button
				type="button"
				on:click={() => copyEmail(c.email)}
				aria-label={`Copy ${c.email} to clipboard`}
				class="text-left w-full rounded-[14px] border border-white/[0.12] bg-white/[0.06] px-[21px] py-[19px] flex flex-col gap-[3px] hover:bg-white/[0.1] transition-colors"
			>
				<span class="font-hg-body font-semibold text-xs uppercase tracking-[0.096em] text-[#8a93a3]">
					{copied === c.email ? 'Copied to clipboard' : c.label}
				</span>
				<span class="text-white text-[17px] font-semibold">{c.email}</span>
			</button>
		{/each}
	</div>
</div>
