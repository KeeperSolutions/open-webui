<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { Popover } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
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

		<Popover.Content
			side="bottom"
			align="end"
			sideOffset={6}
			collisionPadding={12}
			strategy="fixed"
			fitViewport={true}
			transition={flyAndScale}
			class="z-[9999] rounded-2xl border border-hg-border dark:border-gray-800 bg-hg-bg-surface dark:bg-gray-900 shadow-xl overflow-hidden"
		>
			<MaskedValuesList {items} />
		</Popover.Content>
	</Popover.Root>
{/if}
