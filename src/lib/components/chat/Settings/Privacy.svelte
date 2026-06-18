<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import { settings, user } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';
	import Switch from '$lib/components/common/Switch.svelte';
	import { getPiiMaskingDefault, isPiiPipelineConfigured, PII_FILTER_IDS } from '$lib/utils/pii';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	// Data types the PII masking pipeline detects, grouped for display. Mirrors the
	// recognizers in pii_filter.py (PRESIDIO_TO_STANDARD). Counts shown in the UI are
	// derived from each group's length, so adding/removing an entry keeps them in sync.
	const detectedDataTypes: { label: string; items: string[] }[] = [
		{
			label: 'Identity',
			items: [
				'Person names',
				'HR OIB',
				'HR JMBG',
				'IE PPSN',
				'RO CNP',
				'UK NINO',
				'UK UTR',
				'UK NHS',
				'US SSN',
				'US EIN'
			]
		},
		{
			label: 'Financial',
			items: ['HR IBAN', 'IE IBAN', 'RO IBAN', 'GB IBAN', 'EU IBANs', 'Credit cards']
		},
		{
			label: 'Contact & Location',
			items: ['Email addresses', 'Phone numbers', 'Locations']
		}
	];

	let piiMaskingEnabled = true;

	// Admin-only sanity check: is a PII filter pipeline actually wired up anywhere
	// (local or cloud)? If masking is on but none is connected, nothing gets masked.
	const isAdmin = $user?.role === 'admin';
	let piiPipelineConfigured = true;
	let piiCheckDone = false;

	$: showPiiPipelineWarning =
		isAdmin && piiCheckDone && piiMaskingEnabled && !piiPipelineConfigured;

	onMount(async () => {
		piiMaskingEnabled = getPiiMaskingDefault($settings);

		if (isAdmin) {
			piiPipelineConfigured = await isPiiPipelineConfigured(localStorage.token);
			piiCheckDone = true;
		}
	});
</script>

<form
	id="tab-privacy"
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		const s = ($settings ?? {}) as any;
		const pipelines = (s.pipelines ?? {}) as Record<string, any>;
		const existingValves = (pipelines.valves ?? {}) as Record<string, any>;

		const valves: Record<string, any> = { ...existingValves };
		for (const id of PII_FILTER_IDS) {
			valves[id] = {
				...(existingValves[id] ?? {}),
				pii_masking_enabled: piiMaskingEnabled
			};
		}

		// Persist directly to avoid the shared saveSettings model refresh, which
		// blocks on /api/models and stalls the toast when a provider (e.g. Ollama)
		// is unreachable.
		const next = { ...s, pipelines: { ...pipelines, valves } };
		await settings.set(next);
		await updateUserSettings(localStorage.token, { ui: next });
		dispatch('save');
	}}
>
	<div class="py-1 overflow-y-scroll max-h-[28rem] md:max-h-full">
		<div>
			<div class="flex items-center justify-between mb-1">
				<div id="pii-masking-label" class="text-sm font-medium">
					{$i18n.t('Enable PII masking')}
				</div>

				<div class="">
					<Switch ariaLabelledbyId="pii-masking-label" bind:state={piiMaskingEnabled} />
				</div>
			</div>

			<div class="text-xs text-gray-500">
				{$i18n.t(
					'When ON, personal data (names, IBANs, emails, etc.) is automatically replaced with placeholders before being sent to the AI model.'
				)}
			</div>

			{#if showPiiPipelineWarning}
				<div
					class="mt-2 flex items-start gap-2 rounded-lg bg-yellow-50 px-3 py-2 text-xs text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-200"
					role="alert"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="size-4 shrink-0 mt-0.5"
					>
						<path
							fill-rule="evenodd"
							d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495ZM10 5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 5Zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
							clip-rule="evenodd"
						/>
					</svg>
					<span>
						{$i18n.t(
							'PII masking is enabled, but no PII filter pipeline is connected (locally or in the cloud). Messages will not be masked.'
						)}
					</span>
				</div>
			{/if}
		</div>

		<div class="mt-6">
			<div class="text-sm font-medium mb-3">Detected data types</div>

			<div class="space-y-4">
				{#each detectedDataTypes as group}
					<div>
						<div class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">
							{group.label} ({group.items.length})
						</div>
						<div class="flex flex-wrap gap-2">
							{#each group.items as item}
								<span
									class="px-3.5 py-1 rounded-full text-sm bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200"
									>{item}</span
								>
							{/each}
						</div>
					</div>
				{/each}
			</div>
		</div>
	</div>

	<div class="flex justify-end text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
