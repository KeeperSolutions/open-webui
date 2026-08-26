<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { Popover } from 'bits-ui';
	import HgIconShield from '$lib/components/icons/HgIconShield.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import MaskedValuesList from './MaskedValuesList.svelte';

	const i18n =
		getContext<Writable<{ t: (key: string, vars?: Record<string, unknown>) => string }>>('i18n');

	type PiiItem = { key: string; type: string; value: string };

	export let detections: { type: string; start: number; end: number }[] = [];
	export let originalText = '';

	let show = false;
	let items: PiiItem[] = [];
	let count = 0;

	// Reconstruct masked values locally from the user's own message text and
	// dedupe identical (type, value) pairs. Nothing here leaves the browser:
	// the wire/DB only ever carried {type, start, end}.
	$: items = Array.from(
		new Map(
			(detections ?? [])
				.map((d): [string, PiiItem] => {
					const value = (originalText ?? '').slice(d.start, d.end);
					// JSON.stringify gives an unambiguous (type, value) key — a plain
					// `${type}::${value}` join could collide if a value contained "::".
					const key = JSON.stringify([d.type, value]);
					return [key, { key, type: d.type, value }];
				})
				.filter(([, it]) => it.value !== '')
		).values()
	);
	$: count = items.length;
</script>

{#if count > 0}
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
