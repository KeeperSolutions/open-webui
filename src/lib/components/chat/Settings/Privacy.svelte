<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import { settings, user } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';
	import { getSessionUser } from '$lib/apis/auths';
	import Switch from '$lib/components/common/Switch.svelte';
	import { getPiiMaskingDefault, isPiiPipelineConfigured, piiFilterIds } from '$lib/utils/pii';

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
				'Usernames',
				'IE PPSN',
				'UK NINO',
				'UK UTR',
				'UK NHS',
				'US SSN',
				'US EIN',
				'HR OIB',
				'HR JMBG',
				'RO CNP'
			]
		},
		{
			label: 'Financial',
			items: ['EU IBANs', 'IE IBAN', 'GB IBAN', 'HR IBAN', 'RO IBAN', 'Credit cards']
		},
		{
			label: 'Contact & Location',
			items: ['Email addresses', 'Phone numbers', 'IP addresses', 'Postal address']
		},
		{
			label: 'Secrets',
			items: ['Passwords', 'API keys', 'Tokens']
		}
	];

	/**
	 * The user's OWN stored preference — and the only value this form ever
	 * persists.
	 *
	 * ⚠️ The team policy must never reach this variable. It is assigned in
	 * exactly two places: from the stored settings on mount, and by the user's
	 * own toggle — which is bound only in the unlocked branch of the markup
	 * below. The locked branch renders a display-only Switch with no binding at
	 * all, so there is no code path by which the policy can turn a stored `false`
	 * into a persisted `true`.
	 *
	 * That is the structural half of the invariant. The `submit` handler additionally
	 * skips the masking valves entirely while locked, but the invariant does not
	 * depend on that guard surviving: delete it and Save merely writes the stored
	 * value back unchanged.
	 */
	let piiMaskingEnabled = true;

	// Team policy. Read-only overlay: it decides what is DISPLAYED and
	// whether the control is locked; it never decides what is STORED.
	$: policyEnforced = $user?.permissions?.chat?.pii_masking_enforced ?? false;

	// Admin-only sanity check: is a PII filter pipeline actually wired up anywhere
	// (local or cloud)? If masking is on but none is connected, nothing gets masked.
	const isAdmin = $user?.role === 'admin';
	let piiPipelineConfigured = true;
	let piiCheckDone = false;

	$: showPiiPipelineWarning =
		isAdmin && piiCheckDone && (policyEnforced || piiMaskingEnabled) && !piiPipelineConfigured;

	onMount(async () => {
		piiMaskingEnabled = getPiiMaskingDefault($settings);

		// `$user.permissions` is only refilled on a full page load, so a tab
		// left open carries a stale policy for hours. Refresh it here — but ONLY
		// while the control is unlocked. The asymmetry is deliberate: showing an
		// unlocked toggle that the backend already overrides is a security-facing
		// lie, while showing a locked one a little too long is an inconvenience.
		// If it is already locked there is nothing stale to correct, so no call.
		if (!policyEnforced) {
			const sessionUser = await getSessionUser(localStorage.token).catch(() => null);
			if (sessionUser) {
				await user.set(sessionUser);
			}
		}

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
		// ⚠️ While the policy locks the control, Save must not touch a single
		// masking valve. Otherwise a user whose stored value is `false`, shown a
		// locked `ON` switch, would destroy their own preference by pressing Save
		// — and the policy would have written into user.settings by proxy, which
		// is exactly what the policy-never-writes invariant forbids.
		if (!policyEnforced) {
			// The same list the reader uses. If the writer stayed on the built-in
			// constant, an operator who adds an id would leave that filter without a
			// stored valve — the backend would fall back to its default and the
			// user's "off" would be silently ignored for it.
			for (const id of piiFilterIds()) {
				valves[id] = {
					...(existingValves[id] ?? {}),
					pii_masking_enabled: piiMaskingEnabled
				};
			}
		}

		// Persist directly to avoid the shared saveSettings model refresh, which
		// blocks on /api/models and stalls the toast when a provider (e.g. Ollama)
		// is unreachable.
		const next = { ...s, pipelines: { ...pipelines, valves } };
		// ⚠️ Server first, store second. The switch is driven by local state, so
		// nothing on screen waits for this — but anything watching `$settings` as a
		// signal to re-read the server (the PII dashboard does) would otherwise
		// fire against the OLD value and cache it. Store-then-persist looks
		// optimistic; here it is just a race with no upside.
		await updateUserSettings(localStorage.token, { ui: next });
		await settings.set(next);
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
					{#if policyEnforced}
						<!--
							Locked by team policy. `inert` blocks pointer AND keyboard and
							removes the control from the accessibility tree, which is what a
							`disabled` prop would have done — without editing the upstream
							Switch atom, whose styling then keeps tracking upstream.
							The Switch is rendered WITHOUT `bind:` on purpose: see the note above.
						-->
						<div
							inert
							aria-disabled="true"
							aria-describedby="pii-masking-policy-reason"
							data-testid="pii-masking-lock"
							class="opacity-60 cursor-not-allowed"
						>
							<Switch ariaLabelledbyId="pii-masking-label" state={true} />
						</div>
					{:else}
						<Switch ariaLabelledbyId="pii-masking-label" bind:state={piiMaskingEnabled} />
					{/if}
				</div>
			</div>

			<div class="text-xs text-gray-500">
				{$i18n.t(
					'When ON, personal data (names, IBANs, emails, etc.) is automatically replaced with placeholders before being sent to the AI model.'
				)}
			</div>

			{#if policyEnforced}
				<!--
					A locked control needs a reason. The `temporary_enforced` precedent
					HIDES its control instead; that is not copied here, because a toggle
					that was visible yesterday and gone today reads as a bug, not a policy.
					Shape reused from the pipeline warning below; neutral colours, because
					a policy is not a malfunction.
				-->
				<div
					id="pii-masking-policy-reason"
					class="mt-2 flex items-start gap-2 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-700 dark:bg-gray-850 dark:text-gray-300"
					role="note"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="size-4 shrink-0 mt-0.5"
					>
						<path
							fill-rule="evenodd"
							d="M10 1a4.5 4.5 0 0 0-4.5 4.5V9H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-.5V5.5A4.5 4.5 0 0 0 10 1Zm3 8V5.5a3 3 0 1 0-6 0V9h6Z"
							clip-rule="evenodd"
						/>
					</svg>
					<span>
						{$i18n.t(
							'PII masking is enforced by your organisation’s policy and cannot be turned off. Contact your administrator if this needs to change.'
						)}
					</span>
				</div>
			{/if}

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
