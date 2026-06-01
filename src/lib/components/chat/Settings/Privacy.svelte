<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { settings } from '$lib/stores';
	import Switch from '$lib/components/common/Switch.svelte';

	const PII_FILTER_ID = 'pii_filter';

	const i18n = getContext('i18n');

	export let saveSettings: Function;

	let piiMaskingEnabled = true;

	onMount(() => {
		const s = $settings as any;
		piiMaskingEnabled = s?.pipelines?.valves?.[PII_FILTER_ID]?.pii_masking_enabled ?? true;
	});

	async function onPiiMaskingChange() {
		const s = $settings as any;
		const pipelines = (s?.pipelines ?? {}) as Record<string, any>;
		const valves = (pipelines.valves ?? {}) as Record<string, any>;
		const filterValves = (valves[PII_FILTER_ID] ?? {}) as Record<string, any>;

		await saveSettings({
			pipelines: {
				...pipelines,
				valves: {
					...valves,
					[PII_FILTER_ID]: {
						...filterValves,
						pii_masking_enabled: piiMaskingEnabled
					}
				}
			}
		});
	}
</script>

<form
	id="tab-privacy"
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault
>
	<div class="py-1 overflow-y-scroll max-h-[28rem] md:max-h-full">
		<div>
			<div class="flex items-center justify-between mb-1">
				<div class="text-sm font-medium">
					{$i18n.t('Enable PII masking')}
				</div>

				<div class="">
					<Switch bind:state={piiMaskingEnabled} on:change={onPiiMaskingChange} />
				</div>
			</div>

			<div class="text-xs text-gray-500">
				{$i18n.t(
					'When ON, personal data (names, OIBs, IBANs, emails, etc.) is automatically replaced with placeholders before being sent to the AI model.'
				)}
			</div>
		</div>
	</div>
</form>
