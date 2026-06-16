<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { slide } from 'svelte/transition';
	import HgIconShield from '$lib/components/icons/HgIconShield.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

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
	<div class="relative self-center">
		<button
			type="button"
			class="flex items-center gap-1.5 text-sm px-2 py-1 rounded-lg text-gray-600 dark:text-gray-300 hover:bg-black/5 dark:hover:bg-white/5 transition"
			on:click={() => (show = !show)}
			aria-expanded={show}
		>
			<HgIconShield class="size-4 text-green-600 dark:text-green-400" />
			<span class="font-medium whitespace-nowrap"
				>{$i18n.t('{{count}} values masked', { count })}</span
			>
			<ChevronDown
				className="size-3 transition-transform duration-150 {show ? 'rotate-180' : ''}"
			/>
		</button>

		{#if show}
			<div
				transition:slide={{ duration: 150 }}
				class="absolute right-0 z-20 mt-1 w-72 max-w-[80vw] p-3 rounded-lg border border-gray-50 dark:border-gray-850 bg-white dark:bg-gray-900 shadow-lg text-sm text-left"
			>
				<div class="flex flex-col gap-1.5">
					{#each items as it (it.key)}
						<div class="flex items-center gap-2 flex-wrap">
							<span
								class="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200"
							>
								{it.type}
							</span>
							<span class="font-mono text-gray-600 dark:text-gray-300 break-all">{it.value}</span>
						</div>
					{/each}
				</div>
				<div
					class="mt-2 pt-2 border-t border-gray-50 dark:border-gray-850 text-xs text-gray-400 dark:text-gray-500"
				>
					{$i18n.t('Values are reconstructed locally — they never leave your browser.')}
				</div>
			</div>
		{/if}
	</div>
{/if}
