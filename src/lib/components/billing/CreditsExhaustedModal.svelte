<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher<{ close: void }>();
</script>

<div
	class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
	on:click|self={() => dispatch('close')}
	on:keydown|self={(e) => { if (e.key === 'Escape') dispatch('close'); }}
	role="dialog"
	aria-modal="true"
	tabindex="-1"
>
	<div class="bg-white dark:bg-gray-900 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 space-y-4">
		<div class="flex items-start gap-3">
			<span class="text-2xl" aria-hidden="true">⚠</span>
			<div>
				<h2 class="font-semibold text-base">{$i18n.t('Credits exhausted')}</h2>
				<p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
					{$i18n.t("You've used all your credits for this month. Top up or upgrade to continue chatting.")}
				</p>
			</div>
		</div>

		<div class="flex gap-2">
			<a
				href="/billing"
				on:click={() => dispatch('close')}
				class="flex-1 text-center py-2.5 rounded-lg text-sm bg-blue-600 text-white hover:bg-blue-700 transition font-medium"
			>
				{$i18n.t('Go to billing →')}
			</a>
			<button
				on:click={() => dispatch('close')}
				class="flex-1 py-2.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 transition font-medium"
			>
				{$i18n.t('Dismiss')}
			</button>
		</div>
	</div>
</div>
