<script lang="ts">
	// Presentational, container-agnostic view of locally-reconstructed PII values.
	// Renders inside the masked-values popover today; the same component can later
	// be dropped into a side panel / drawer without changes.
	import { getContext, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { copyToClipboard } from '$lib/utils';

	const i18n =
		getContext<Writable<{ t: (key: string, vars?: Record<string, unknown>) => string }>>('i18n');

	type MaskedItem = { key: string; type: string; value: string };

	export let items: MaskedItem[] = [];
	// Above this many items the view switches to a grouped + searchable density.
	export let groupThreshold = 8;

	let query = '';
	let copiedKey: string | null = null;
	let copyTimer: ReturnType<typeof setTimeout> | null = null;

	// Entity types arrive as machine codes (PERSON, US_SSN, HR_OIB...). Underscores
	// → spaces keeps the acronyms readable without inventing a translation table.
	const formatType = (t: string) => (t ?? '').replace(/_/g, ' ');

	$: dense = items.length > groupThreshold;
	$: q = query.trim().toLowerCase();
	$: filtered = q
		? items.filter(
				(it) => it.value.toLowerCase().includes(q) || formatType(it.type).toLowerCase().includes(q)
			)
		: items;

	// Stable, alphabetical groups keyed by entity type. When the list is small we
	// collapse everything into one unlabeled section so it reads as a flat list.
	$: groups = (() => {
		const map = new Map<string, { type: string; label: string; items: MaskedItem[] }>();
		for (const it of filtered) {
			const g = map.get(it.type) ?? { type: it.type, label: formatType(it.type), items: [] };
			g.items.push(it);
			map.set(it.type, g);
		}
		return [...map.values()].sort((a, b) => a.label.localeCompare(b.label));
	})();
	$: sections = dense ? groups : [{ type: '__all__', label: '', items: filtered }];

	const copyValueToClipboard = async (it: MaskedItem) => {
		const ok = await copyToClipboard(it.value);
		if (!ok) return;
		copiedKey = it.key;
		if (copyTimer) clearTimeout(copyTimer);
		copyTimer = setTimeout(() => (copiedKey = null), 1200);
	};

	// Don't let a pending copy-feedback timer outlive the component.
	onDestroy(() => {
		if (copyTimer) clearTimeout(copyTimer);
	});
</script>

<!-- not-prose: this list renders inside a `.markdown-prose` message (via the PII
	popover, which is NOT portalled), so prose heading/list styles would otherwise
	leak in and blow up the <h3> section titles. Opt the whole subtree out. -->
<div
	class="not-prose flex flex-col w-80 max-w-[calc(100vw-1.5rem)] max-h-[min(70vh,28rem)] font-hg-body text-hg-text-primary dark:text-gray-100"
>
	{#if dense}
		<div class="shrink-0 p-2 border-b border-hg-border dark:border-gray-800">
			<input
				type="text"
				bind:value={query}
				autocomplete="off"
				aria-label={$i18n.t('Search masked values')}
				placeholder={$i18n.t('Search masked values')}
				class="w-full rounded-lg py-1.5 px-3 text-sm bg-gray-50 dark:bg-gray-850 dark:text-gray-300 outline-hidden"
			/>
		</div>
	{/if}

	<div class="overflow-y-auto flex-1 p-2">
		{#if filtered.length === 0}
			<div class="py-6 text-center text-xs text-hg-text-tertiary dark:text-gray-500">
				{$i18n.t('No results found')}
			</div>
		{:else}
			{#each sections as g (g.type)}
				<section class="mb-2 last:mb-0">
					{#if dense}
						<h3
							class="flex items-center gap-2 px-2 mb-1 text-[11px] font-semibold uppercase tracking-wide text-hg-text-tertiary dark:text-gray-500"
						>
							<span>{g.label}</span>
							<span class="text-hg-text-tertiary dark:text-gray-600">{g.items.length}</span>
						</h3>
					{/if}
					<ul class="flex flex-col gap-1" aria-label={dense ? g.label : undefined}>
						{#each g.items as it (it.key)}
							<li
								class="group flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-850"
							>
								<span
									class="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-green-50 text-green-800 dark:bg-green-900/40 dark:text-green-300 whitespace-nowrap"
									>{formatType(it.type)}</span
								>
								<span
									class="min-w-0 flex-1 font-mono text-xs text-hg-text-secondary dark:text-gray-300 break-all"
									title={it.value}>{it.value}</span
								>
								<button
									type="button"
									class="shrink-0 p-1 rounded-md text-hg-text-tertiary dark:text-gray-500 hover:text-hg-text-primary dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition"
									aria-label={$i18n.t('Copy value')}
									on:click={() => copyValueToClipboard(it)}
								>
									{#if copiedKey === it.key}
										<svg
											xmlns="http://www.w3.org/2000/svg"
											fill="none"
											viewBox="0 0 24 24"
											stroke-width="2.2"
											stroke="currentColor"
											class="size-3.5"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												d="M4.5 12.75l6 6 9-13.5"
											/>
										</svg>
									{:else}
										<svg
											xmlns="http://www.w3.org/2000/svg"
											fill="none"
											viewBox="0 0 24 24"
											stroke-width="1.8"
											stroke="currentColor"
											class="size-3.5"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184"
											/>
										</svg>
									{/if}
								</button>
							</li>
						{/each}
					</ul>
				</section>
			{/each}
		{/if}
	</div>

	<div
		class="shrink-0 px-3 py-2 border-t border-hg-border-subtle dark:border-gray-850 text-[11px] text-hg-text-tertiary dark:text-gray-500"
	>
		{$i18n.t('Values are reconstructed locally — they never leave your browser.')}
	</div>
</div>
