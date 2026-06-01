<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import { settings } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';
	import Switch from '$lib/components/common/Switch.svelte';

	// Known PII filter pipeline IDs across deployed instances:
	//   "pii_filter"           — pii-filter repo (pii_filter.py)
	//   "pii_filter_pipeline"  — pipelines-v4 repo + GCP deployment (pii_filter_pipeline.py)
	// The OpenWebUI bridge keys per-filter valves by the runtime filter id, so we
	// mirror the toggle under every known id to cover whichever pipeline is wired up.
	const PII_FILTER_IDS = ['pii_filter', 'pii_filter_pipeline'] as const;

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	let piiMaskingEnabled = true;

	onMount(() => {
		const valves = ($settings as any)?.pipelines?.valves ?? {};
		for (const id of PII_FILTER_IDS) {
			const v = valves?.[id]?.pii_masking_enabled;
			if (typeof v === 'boolean') {
				piiMaskingEnabled = v;
				return;
			}
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
				<div class="text-sm font-medium">
					{$i18n.t('Enable PII masking')}
				</div>

				<div class="">
					<Switch bind:state={piiMaskingEnabled} />
				</div>
			</div>

			<div class="text-xs text-gray-500">
				{$i18n.t(
					'When ON, personal data (names, IBANs, emails, etc.) is automatically replaced with placeholders before being sent to the AI model.'
				)}
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
