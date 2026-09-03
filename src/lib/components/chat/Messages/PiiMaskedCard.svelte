<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { Popover } from 'bits-ui';
	import HgIconShield from '$lib/components/icons/HgIconShield.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import MaskedValuesList from './MaskedValuesList.svelte';

	const i18n =
		getContext<Writable<{ t: (key: string, vars?: Record<string, unknown>) => string }>>('i18n');

	type PiiDetection = {
		type: string;
		start: number;
		end: number;
		// B2: file-sourced detections carry the file + chunk they came from so the
		// value can be reconstructed from the citation chunk the browser already
		// has. The wire/DB still never carry a plaintext value.
		fileId?: string;
		fileName?: string;
		docIdx?: number;
	};
	type PiiItem = { key: string; type: string; value: string; source?: string };
	type CitationSource = {
		source?: { id?: string; name?: string; type?: string };
		document?: string[];
		metadata?: { file_id?: string }[];
	};

	export let detections: PiiDetection[] = [];
	export let originalText = '';
	// Citation sources for the same response — used to reconstruct file-sourced
	// PII values locally (the original chunk text the user already received).
	export let sources: CitationSource[] = [];
	// Ingest-time file PII: already-reconstructed items from the full file content,
	// fetched by UserMessage from GET /files/{id}/data/content.
	export let fileItems: PiiItem[] = [];
	export let scanning = false;

	let show = false;
	let items: PiiItem[] = [];
	let count = 0;

	// Locate the original (unmasked) chunk a file-sourced detection points at, by
	// matching fileId against the citation sources, then slice the value out of
	// it. Returns '' when the chunk can't be located (filtered out below).
	const reconstructFileValue = (d: PiiDetection): string => {
		const src = (sources ?? []).find(
			(s) => s?.source?.id === d.fileId || (s?.metadata ?? []).some((m) => m?.file_id === d.fileId)
		);
		const chunk = src?.document?.[d.docIdx ?? -1];
		return typeof chunk === 'string' ? chunk.slice(d.start, d.end) : '';
	};

	// Reconstruct masked values locally and dedupe identical (type, value, source)
	// triples. Nothing sensitive leaves the browser: the wire/DB only ever carried
	// {type, start, end} (+ file/chunk refs for file-sourced detections).
	// Map insertion order matters: file/ingest items come LAST so they win on a
	// key collision — the ingest path is the authoritative, complete source of
	// file PII, while the B2/detections path only covers retrieved chunks.
	$: items = Array.from(
		new Map([
			// B2 / message-sourced items: reconstruct value from originalText or citation chunk
			...(detections ?? [])
				.map((d): [string, PiiItem] => {
					const isFile = d.fileId != null;
					const value = isFile
						? reconstructFileValue(d)
						: (originalText ?? '').slice(d.start, d.end);
					const source = isFile ? d.fileName : undefined;
					// JSON.stringify gives an unambiguous (type, value, source) key.
					const key = JSON.stringify([d.type, value, source ?? null]);
					return [key, { key, type: d.type, value, source }];
				})
				.filter(([, it]) => it.value !== ''),
			// ingest-path items (already reconstructed, carry value + source) —
			// authoritative on collision, so spread LAST.
			...(fileItems ?? []).map((it): [string, PiiItem] => [it.key, it])
		]).values()
	).filter((it) => it.value !== '');
	$: count = items.length;
</script>

{#if scanning && count === 0}
	<div
		class="flex items-center gap-1 h-9 px-3 py-1 self-center rounded-full bg-stone-50 dark:bg-gray-800"
	>
		<HgIconShield class="size-3.5 text-hg-text-secondary dark:text-gray-400 animate-pulse" />
		<span
			class="font-hg-body text-xs font-normal text-hg-text-secondary dark:text-gray-400 whitespace-nowrap"
			>{$i18n.t('PII scan in progress…')}</span
		>
	</div>
{:else if count > 0}
	<Popover.Root bind:open={show}>
		<!-- Badge — Figma "PiiMaskingResult" pill; chevron flips while open -->
		<Popover.Trigger
			class="flex items-center justify-center gap-1 h-9 px-3 py-1 self-center rounded-full bg-stone-50 dark:bg-gray-800 hover:bg-stone-100 dark:hover:bg-gray-700 transition"
		>
			<HgIconShield class="size-3.5 text-hg-success-600 dark:text-green-400" />
			<span
				class="font-hg-body text-xs font-normal text-hg-text-primary dark:text-gray-100 whitespace-nowrap"
				>{$i18n.t('{{count}} values masked', { count })}</span
			>
			<ChevronDown
				className="size-4 text-hg-text-secondary dark:text-gray-400 transition-transform duration-150 {show
					? 'rotate-180'
					: ''}"
			/>
		</Popover.Trigger>

		<!-- Portal is required, not cosmetic: the chat column is a Tailwind
			`@container` (container-type: inline-size), which makes it the containing
			block for position:fixed descendants. Rendered in place, the panel's
			viewport coordinates get offset by the column's origin and it lands off
			the right edge of the screen. bits-ui portalled Content by default in
			0.21; since 2.x it is opt-in. -->
		<Popover.Portal>
			<Popover.Content
				side="bottom"
				align="end"
				sideOffset={6}
				collisionPadding={12}
				strategy="fixed"
				class="pii-masked-panel z-[9999] rounded-2xl border border-hg-border dark:border-gray-800 bg-hg-bg-surface dark:bg-gray-900 shadow-xl overflow-hidden"
			>
				<MaskedValuesList {items} />
			</Popover.Content>
		</Popover.Portal>
	</Popover.Root>
{/if}

<style>
	/* Open/close motion. bits-ui 0.21 drove this through a `transition` prop; 2.x
		removed it and instead marks the content with data-starting-style (first
		frame open) and data-ending-style (while closing), holding the unmount until
		the animation finishes. Values mirror the flyAndScale the card used before:
		y -8px, scale 0.95, 200ms cubicOut. Scaling from the floating origin makes it
		grow out of the badge rather than out of thin air.

		:global is required — the panel is portalled to <body>, so it is outside this
		component's subtree and scoped selectors would never reach it. The class is
		card-specific to avoid touching any other popover. */
	:global(.pii-masked-panel) {
		opacity: 1;
		transform: translateY(0) scale(1);
		transform-origin: var(--bits-popover-content-transform-origin, center);
		transition:
			opacity 200ms cubic-bezier(0.33, 1, 0.68, 1),
			transform 200ms cubic-bezier(0.33, 1, 0.68, 1);
	}

	:global(.pii-masked-panel[data-starting-style]),
	:global(.pii-masked-panel[data-ending-style]) {
		opacity: 0;
		transform: translateY(-8px) scale(0.95);
	}

	@media (prefers-reduced-motion: reduce) {
		:global(.pii-masked-panel) {
			transition: none;
		}

		/* Land on the final values straight away instead of flashing the offset
			start/end frame with the transition switched off. */
		:global(.pii-masked-panel[data-starting-style]),
		:global(.pii-masked-panel[data-ending-style]) {
			opacity: 1;
			transform: none;
		}
	}
</style>
