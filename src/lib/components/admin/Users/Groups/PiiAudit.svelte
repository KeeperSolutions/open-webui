<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	// Typed rather than the bare `getContext('i18n')` the sibling files use: that
	// form is what produces the "Cannot use 'i18n' as a store" errors all over
	// this directory, and there is no reason to add seven more in a new file.
	const i18n: Writable<i18nType> = getContext('i18n');

	import dayjs from 'dayjs';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	dayjs.extend(localizedFormat);

	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		getGroupPiiAudit,
		type PiiPolicyAuditEvent,
		type PiiPolicyAuditEventType
	} from '$lib/apis/groups/piiAudit';

	/** Absent for the default-permissions modal, which has no group to report on. */
	export let groupId: string | undefined = undefined;

	let loading = true;
	let failed = false;
	let events: PiiPolicyAuditEvent[] = [];
	let total = 0;

	// 🎨 No design source for this panel; the shapes below are the
	// admin modal's own idiom — xs text, muted greys — not new atoms.
	const LABELS: Record<PiiPolicyAuditEventType, string> = {
		policy_enabled: 'Enforcement turned on',
		policy_disabled: 'Enforcement turned off',
		member_added: 'Added {{user}} to the group',
		member_removed: 'Removed {{user}} from the group'
	};

	// Both removals from protection. Marked so the eye finds them, since they are
	// the two that had to state a reason.
	const REMOVALS: PiiPolicyAuditEventType[] = ['policy_disabled', 'member_removed'];

	// The account can be gone by the time anyone reads this: only the ACTOR's
	// email is stored on the row, so a deleted target degrades to its id rather
	// than to nothing.
	const who = (event: PiiPolicyAuditEvent) => event.user_email || event.user_id || '';

	onMount(async () => {
		if (!groupId) {
			loading = false;
			return;
		}
		const res = await getGroupPiiAudit(localStorage.token, groupId).catch(() => {
			failed = true;
			return null;
		});
		if (res) {
			events = res.items ?? [];
			total = res.total ?? 0;
		}
		loading = false;
	});
</script>

{#if groupId}
	<div class="flex flex-col w-full mt-3">
		<div class="mb-1 text-xs font-medium">{$i18n.t('PII policy history')}</div>

		{#if loading}
			<div class="flex justify-center py-3"><Spinner className="size-4" /></div>
		{:else if failed}
			<div class="text-xs text-gray-500">{$i18n.t('Failed to load the policy history.')}</div>
		{:else if events.length === 0}
			<!-- Never an empty box: "nothing here" and "nothing recorded yet" read
			     the same on screen and mean different things. -->
			<div class="text-xs text-gray-500">
				{$i18n.t('No policy changes have been recorded for this group yet.')}
			</div>
		{:else}
			{#if total > events.length}
				<!-- Truncation is always stated. A compliance list that shows part of
				     itself without saying so asserts something untrue. -->
				<div class="mb-1.5 text-xs text-gray-500">
					{$i18n.t('Showing the latest {{shown}} of {{total}} events.', {
						shown: events.length,
						total
					})}
				</div>
			{/if}

			<div class="flex flex-col gap-1.5 max-h-64 overflow-y-auto scrollbar-hidden">
				{#each events as event (event.id)}
					<div class="rounded-xl bg-gray-50 px-2.5 py-1.5 dark:bg-gray-850">
						<div class="flex items-baseline justify-between gap-2">
							<span
								class="text-xs font-medium {REMOVALS.includes(event.event_type)
									? 'text-red-600 dark:text-red-400'
									: ''}"
							>
								{$i18n.t(LABELS[event.event_type], { user: who(event) })}
							</span>
							<span class="shrink-0 text-[11px] text-gray-500">
								{dayjs(event.event_ts * 1000).format('lll')}
							</span>
						</div>
						<div class="text-[11px] text-gray-500">{event.actor_email}</div>
						{#if event.reason}
							<!-- Shown, never hidden behind a hover: it is the one thing an
							     admin was required to write. -->
							<div class="mt-0.5 text-[11px] text-gray-600 dark:text-gray-400">
								{$i18n.t('Reason')}: {event.reason}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}

		{#if !loading && !failed}
			<div class="mt-1.5 text-[11px] text-gray-500">
				{$i18n.t(
					'Changes made outside these controls — the Users tab, SCIM, or OAuth group mapping — are not recorded here.'
				)}
			</div>
		{/if}
	</div>
{/if}
