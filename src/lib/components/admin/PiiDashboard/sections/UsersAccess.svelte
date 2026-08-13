<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { goto } from '$app/navigation';
	import SectionHeader from '../parts/SectionHeader.svelte';
	import Pill from '../parts/Pill.svelte';
	import Button from '../parts/Button.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import type { MetricRow } from '$lib/apis/langfuse';
	import { formatCostDisplay } from '$lib/apis/langfuse/tableUtils';
	import { toast } from 'svelte-sonner';
	import { addUserToGroup, removeUserFromGroup } from '$lib/apis/groups';
	import {
		buildRows,
		unattributedCost,
		maskingRank,
		rowActionFor,
		type MaskingState,
		type AccessUser,
		type PolicyGroup,
		type UserRow,
		type UserStatus
	} from './usersAccess';
	import type { Truncation } from '../usersAccessLoader';

	const i18n: Writable<i18nType> = getContext('i18n');

	/** The directory. Without it there is no table, so it drives loading/failed. */
	export let users: AccessUser[] = [];
	/** Spend for the selected window. A second, slower source — see `costUnknown`. */
	export let metricRows: MetricRow[] = [];
	export let truncated: Truncation | null = null;
	export let loading = true;
	export let failed = false;
	export let errorDetail: string | null = null;
	export let onRetry: () => void;
	/** Spend has never arrived, or its last fetch failed: show it as unknown. */
	export let costUnknown = false;
	/** Spend on screen belongs to the previous window; a newer one is in flight. */
	export let costStale = false;
	/** Groups that carry the policy — the only destinations an action may use. */
	export let policyGroups: PolicyGroup[] = [];
	/** Reload the section after a membership change. */
	export let onPolicyChanged: () => void = () => {};

	/**
	 * The row currently being acted on, if any.
	 *
	 * ⚠️ Holds the chosen destination for exactly as long as one action takes,
	 * and is cleared afterwards. Nothing about the choice survives the call — not
	 * in component state between calls, not in `localStorage`. A remembered
	 * destination would be per-admin, so the same action would land in different
	 * groups depending on who clicked it, with nothing recording why (E-2).
	 */
	let pending: {
		row: UserRow;
		mode: 'enforce' | 'remove';
		targets: PolicyGroup[];
		groupId: string;
		reason: string;
	} | null = null;
	let submitting = false;

	const openEnforce = (row: UserRow, targets: PolicyGroup[]) => {
		pending = {
			row,
			mode: 'enforce',
			targets,
			// Pre-selected only when there is exactly one; with several the admin
			// must say which, every time.
			groupId: targets.length === 1 ? targets[0].id : '',
			reason: ''
		};
	};

	const openRemove = (row: UserRow, group: PolicyGroup) => {
		pending = { row, mode: 'remove', targets: [group], groupId: group.id, reason: '' };
	};

	const submitAction = async () => {
		if (!pending || !pending.groupId) return;
		// The route enforces this too; here it only saves a round trip (D-6).
		if (pending.mode === 'remove' && !pending.reason.trim()) return;

		submitting = true;
		const { row, mode, groupId, reason } = pending;
		const call =
			mode === 'enforce'
				? addUserToGroup(localStorage.token, groupId, [row.id])
				: removeUserFromGroup(localStorage.token, groupId, [row.id], reason.trim());

		const res = await call.catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		submitting = false;

		if (res) {
			toast.success(
				mode === 'enforce'
					? $i18n.t('PII masking is now enforced for this user.')
					: $i18n.t('PII masking is no longer enforced for this user.')
			);
			pending = null;
			// Re-read rather than patch the row: the effective policy is a server-side
			// merge over every group, so the only honest new value comes from the server.
			onPolicyChanged();
		}
	};

	type SortKey = 'name' | 'role' | 'status' | 'masking' | 'cost';

	let orderBy: SortKey = 'cost';
	let direction: 'asc' | 'desc' = 'desc';

	// Accepts null so the header loop can call it uniformly; a null key is the
	// marker for a column that does not sort.
	const setSortKey = (key: SortKey | null) => {
		if (key === null) return;
		if (orderBy === key) {
			direction = direction === 'asc' ? 'desc' : 'asc';
		} else {
			orderBy = key;
			// Cost reads most usefully largest-first; everything else alphabetically.
			direction = key === 'cost' ? 'desc' : 'asc';
		}
	};

	const ROLE_LABELS: Record<string, string> = {
		admin: 'Admin',
		user: 'User',
		pending: 'Pending'
	};

	// What needs an admin's attention sorts to the top.
	const STATUS_RANK: Record<UserStatus, number> = { pending: 0, inactive: 1, active: 2 };
	const STATUS_KIND: Record<UserStatus, 'info' | 'warn' | 'ok'> = {
		pending: 'info',
		inactive: 'warn',
		active: 'ok'
	};
	const STATUS_LABELS: Record<UserStatus, string> = {
		pending: 'Pending',
		inactive: 'Inactive',
		active: 'Active'
	};

	/**
	 * Sorting takes the key and direction as arguments rather than closing over
	 * them, so the reactive statement below lists them as dependencies. Closing
	 * over them compiles to a sort that only re-runs when the rows change — the
	 * header arrow moves and the order does not.
	 */
	const sortRows = (list: UserRow[], key: SortKey, dir: 'asc' | 'desc'): UserRow[] => {
		const sign = dir === 'asc' ? 1 : -1;
		return [...list].sort((a, b) => {
			let primary = 0;
			switch (key) {
				case 'name':
					primary = a.name.localeCompare(b.name);
					break;
				case 'role':
					primary = a.role.localeCompare(b.role);
					break;
				case 'status':
					primary = STATUS_RANK[a.status] - STATUS_RANK[b.status];
					break;
				case 'masking':
					primary = maskingRank(a.masking) - maskingRank(b.masking);
					break;
				case 'cost':
					primary = a.cost - b.cost;
					break;
			}
			// Ties resolve by name so the order does not shuffle between renders.
			return primary !== 0 ? primary * sign : a.name.localeCompare(b.name);
		});
	};

	// No catalogue is passed: the models column is gone (Gate 6d) and its fetch
	// with it (6e). `buildRows` keeps the parameter and its default, so
	// `grantedCount`/`allModels` stay computed-from-nothing rather than deleted —
	// the next thing to report model access resolves them the same way.
	$: rows = buildRows(users, metricRows);

	$: sorted = sortRows(rows, orderBy, direction);

	/**
	 * Spend the rows above do not account for.
	 *
	 * Kept exhaustive with the column on purpose: every metric row lands either
	 * in a user row or in here, so the two together equal section 3's total.
	 * When the list is cut off this figure additionally absorbs the spend of
	 * users beyond the ceiling — the arithmetic still balances, but the reason
	 * changes, so the copy does too.
	 */
	$: unattributed = unattributedCost(metricRows, users);

	$: unattributedSubline = truncated
		? 'Cost not matched to a user above, including the users not listed'
		: 'Cost from activity that matches no user in this directory';

	$: unattributedTitle = truncated
		? 'The list is cut off, so this also covers users who exist but are not shown, alongside identities with no account here. The column still reconciles with Total cost above.'
		: 'Langfuse recorded this spend against an identity with no matching account here — a deleted user, another environment, or a trace with no user id. It is included so this column reconciles with Total cost above.';

	// `key: null` marks a column that is not sortable. Nothing uses it right now —
	// every remaining column sorts — but the header loop still handles it, so a
	// derived-label column can be added back without touching the markup.
	const COLUMNS: { key: SortKey | null; label: string }[] = [
		{ key: 'name', label: 'User' },
		{ key: 'role', label: 'Role' },
		{ key: 'status', label: 'Status' },
		// There is deliberately no models column. `Workspace models` used to sit
		// here and was removed (Gate 6d): it measured /models/list, which is the
		// Workspace-derived subset, while the question a reader actually asks is
		// "which of the models in this system may this person use". No
		// configuration can answer that today — with BYPASS_MODEL_ACCESS_CONTROL
		// on, grants are ignored and nothing can be restricted; with it off, the
		// polarity inverts and an ungranted model is invisible. The column
		// returns when `denied_model_ids` exists to report against: display
		// follows the mechanism, never precedes it.
		// There is deliberately no `Policy` column. It said `Enforced` for exactly
		// the rows where this one says `On — enforced` — the same boolean twice —
		// and the action cell already names the source. In a compliance table a
		// restatement is not free: the reader assumes two columns measure two
		// things and goes looking for a difference that does not exist.
		{ key: 'masking', label: 'Global PII masking' },
		{ key: 'cost', label: 'Cost' }
	];
</script>

<div class="flex flex-col gap-3.5 font-['Inter']">
	<SectionHeader
		num="4"
		title={$i18n.t('Users & Access')}
		subtitle={$i18n.t('PRD Feature 6 — provisioned users, model permissions, masking posture')}
	/>

	{#if loading}
		<div class="flex min-h-[300px] items-center justify-center">
			<Spinner className="size-5" />
		</div>
	{:else if failed}
		<div class="flex min-h-[300px] flex-col items-center justify-center gap-3 px-4 text-pii-muted">
			<span class="text-[13px]">{$i18n.t('Failed to load users and access.')}</span>
			{#if errorDetail}
				<span class="max-w-[520px] text-center text-[12px] break-words text-pii-muted opacity-80">
					{errorDetail}
				</span>
			{/if}
			<Button variant="primary" on:click={onRetry}>{$i18n.t('Retry')}</Button>
		</div>
	{:else if rows.length === 0}
		<div class="flex min-h-[300px] items-center justify-center text-[13px] text-pii-muted">
			{$i18n.t('No data found')}
		</div>
	{:else}
		<div class="flex flex-col gap-3 rounded-2xl border border-pii-line bg-pii-white p-5">
			{#if truncated}
				<!-- Never truncate silently: the count is the only way to tell a short
				     directory from a cut-off one. -->
				<div
					class="rounded-xl border border-pii-line bg-pii-side px-3.5 py-2.5 text-[12px] text-pii-muted"
				>
					{$i18n.t('Showing the first {{shown}} of {{total}} users. The rest are not listed.', {
						shown: truncated.shown,
						total: truncated.total
					})}
				</div>
			{/if}

			{#if costUnknown}
				<div
					class="rounded-xl border border-pii-line bg-pii-side px-3.5 py-2.5 text-[12px] text-pii-muted"
				>
					{$i18n.t(
						'Cost and status are unavailable for this period. Access and masking are not affected.'
					)}
				</div>
			{/if}

			<!-- Horizontal scroll is the safety net for narrow screens; cells wrap
			     rather than force it, so the table matches the mock at width. -->
			<div class="w-full overflow-x-auto">
				<table class="w-full text-left text-[13px]">
					<thead>
						<tr class="border-b border-pii-line">
							{#each COLUMNS as col}
								{#if col.key === null}
									<th scope="col" class="px-2.5 py-2 text-[11px] font-semibold text-pii-muted">
										{$i18n.t(col.label)}
									</th>
								{:else}
									<th
										scope="col"
										class="cursor-pointer px-2.5 py-2 text-[11px] font-semibold text-pii-muted select-none"
										on:click={() => setSortKey(col.key)}
									>
										<div class="flex items-center gap-1.5">
											{$i18n.t(col.label)}
											{#if orderBy === col.key}
												<span class="font-normal">
													{#if direction === 'asc'}
														<ChevronUp className="size-2" />
													{:else}
														<ChevronDown className="size-2" />
													{/if}
												</span>
											{:else}
												<span class="invisible"><ChevronUp className="size-2" /></span>
											{/if}
										</div>
									</th>
								{/if}
							{/each}
							<!-- Two unlabelled columns: the policy action, then Manage. Both
							     are controls, and neither sorts. -->
							<th scope="col" class="px-2.5 py-2"></th>
							<th scope="col" class="px-2.5 py-2"></th>
						</tr>
					</thead>
					<tbody>
						{#each sorted as row (row.id)}
							<!-- Declared here rather than in the cell that uses it: {@const} must
							     be the immediate child of a block. -->
							{@const action = rowActionFor(row, policyGroups)}
							<tr class="border-b border-pii-line last:border-b-0">
								<td class="px-2.5 py-2.5 align-middle">
									<div class="flex flex-col items-start gap-[3px]">
										<span class="font-bold text-pii-ink">{row.name}</span>
										<span
											class="rounded-md bg-pii-mono-bg px-1.5 py-px font-mono text-[11.5px] text-pii-ink"
											>{row.email}</span
										>
									</div>
								</td>
								<td class="px-2.5 py-2.5 align-middle text-pii-ink">
									{$i18n.t(ROLE_LABELS[row.role] ?? row.role)}
								</td>
								<td
									class="px-2.5 py-2.5 align-middle transition-opacity {costStale
										? 'opacity-40'
										: 'opacity-100'}"
									aria-busy={costStale}
								>
									{#if costUnknown && row.status !== 'pending'}
										<span class="text-pii-muted">—</span>
									{:else}
										<Pill kind={STATUS_KIND[row.status]}>
											{$i18n.t(STATUS_LABELS[row.status])}
										</Pill>
									{/if}
								</td>
								<td class="px-2.5 py-2.5 align-middle">
									<!--
										No Toggle here (D-13): a switch shape is an affordance for
										changing, and this column is a read-only report. The Pill
										carries the whole statement in all four states.
									-->
									{#if row.masking === 'enforced'}
										<Pill kind="ok">🔒 {$i18n.t('On — enforced')}</Pill>
									{:else if row.masking === 'default'}
										<Pill kind="ok">{$i18n.t('On — default')}</Pill>
									{:else if row.masking === 'on'}
										<Pill kind="ok">{$i18n.t('On')}</Pill>
									{:else}
										<Pill kind="warn">{$i18n.t('Off — flagged')}</Pill>
									{/if}
								</td>
								<td
									class="px-2.5 py-2.5 align-middle text-pii-ink transition-opacity {costStale
										? 'opacity-40'
										: 'opacity-100'}"
									aria-busy={costStale}
								>
									{#if costUnknown}
										<span class="text-pii-muted">—</span>
									{:else}
										{formatCostDisplay(row.cost)}
									{/if}
								</td>
								<td class="px-2.5 py-2.5 align-middle">
									<!--
										Membership, never the policy value itself (§8.7). And never an
										action that would not do what its label says: when the policy
										reaches this user through more than one group, or through the
										instance default, removing them from any one of them leaves
										them enforced — so nothing is offered and the source is named
										instead (E-1).
									-->
									{#if action.kind === 'remove'}
										<Button on:click={() => openRemove(row, action.group)}>
											{$i18n.t('Remove')}
										</Button>
									{:else if action.kind === 'enforce'}
										{#if action.targets.length === 0}
											<span
												class="text-[12px] text-pii-muted"
												title={$i18n.t(
													'No group enforces PII masking yet. Turn it on for a group in Admin → Users → Groups → Permissions first.'
												)}
											>
												{$i18n.t('No policy group')}
											</span>
										{:else}
											<Button on:click={() => openEnforce(row, action.targets)}>
												{$i18n.t('Enforce')}
											</Button>
										{/if}
									{:else if action.via.length === 0}
										<span class="text-[12px] text-pii-muted">
											{$i18n.t('Enforced instance-wide')}
										</span>
									{:else}
										<span
											class="text-[12px] text-pii-muted"
											title={$i18n.t(
												'Removing this user from any one of these groups would leave the policy in force through the others, so no action is offered here.'
											)}
										>
											{$i18n.t('Enforced via {{groups}}', {
												groups: action.via.map((g) => g.name).join(', ')
											})}
										</span>
									{/if}
								</td>
								<td class="px-2.5 py-2.5 text-right align-middle">
									<Button on:click={() => goto('/admin/users')}>{$i18n.t('Manage')}</Button>
								</td>
							</tr>
						{/each}
					</tbody>

					{#if !costUnknown && unattributed !== 0}
						<!-- Outside <tbody>, so it is structurally exempt from sorting. -->
						<tfoot>
							<tr
								class="border-t border-pii-line bg-pii-side transition-opacity {costStale
									? 'opacity-40'
									: 'opacity-100'}"
								aria-busy={costStale}
							>
								<td class="px-2.5 py-2.5 align-middle" title={$i18n.t(unattributedTitle)}>
									<div class="flex flex-col items-start gap-[3px]">
										<span class="font-bold text-pii-ink">{$i18n.t('Unattributed')}</span>
										<span class="text-[11.5px] text-pii-muted">{$i18n.t(unattributedSubline)}</span>
									</div>
								</td>
								<td class="px-2.5 py-2.5 align-middle text-pii-muted">—</td>
								<td class="px-2.5 py-2.5 align-middle text-pii-muted">—</td>
								<!-- Masking: no account, so no stored preference and no policy. -->
								<td class="px-2.5 py-2.5 align-middle text-pii-muted">—</td>
								<td class="px-2.5 py-2.5 align-middle font-bold text-pii-ink">
									{formatCostDisplay(unattributed)}
								</td>
								<!-- No policy action and no Manage button: there is no account
								     here to put in a group, let alone to manage. -->
								<td class="px-2.5 py-2.5"></td>
								<td class="px-2.5 py-2.5"></td>
							</tr>
						</tfoot>
					{/if}
				</table>
			</div>
		</div>
	{/if}

	{#if pending}
		<!--
			Deliberately not a bare confirm: the action changes GROUP MEMBERSHIP,
			so the group is named. Losing that group's other permissions is a real
			consequence of the mechanism, and the admin should read it before
			agreeing to it.
		-->
		<div
			class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
			role="presentation"
			on:click|self={() => (pending = null)}
		>
			<div
				class="w-full max-w-[420px] rounded-2xl border border-pii-line bg-pii-white p-5 font-['Inter']"
				role="dialog"
				aria-modal="true"
				aria-label={pending.mode === 'enforce'
					? $i18n.t('Enforce PII masking')
					: $i18n.t('Stop enforcing PII masking')}
			>
				<div class="text-[15px] font-bold text-pii-ink">
					{pending.mode === 'enforce'
						? $i18n.t('Enforce PII masking')
						: $i18n.t('Stop enforcing PII masking')}
				</div>

				<p class="mt-1.5 text-[12.5px] leading-[1.55] text-pii-muted">
					{pending.mode === 'enforce'
						? $i18n.t(
								'{{name}} will be added to the group and will no longer be able to turn PII masking off.',
								{ name: pending.row.name }
							)
						: $i18n.t(
								'{{name}} will be removed from the group and will be able to turn PII masking off again. They also lose everything else that group grants.',
								{ name: pending.row.name }
							)}
				</p>

				{#if pending.mode === 'enforce' && pending.targets.length > 1}
					<!-- More than one group carries the policy, so the destination is
					     asked for on every call and remembered on none (E-2). -->
					<label class="mt-3 block text-[12px] text-pii-muted" for="pii-policy-group">
						{$i18n.t('Which group?')}
					</label>
					<select
						id="pii-policy-group"
						class="mt-1 w-full rounded-xl border border-pii-line bg-pii-white px-2.5 py-1.5 text-[13px] text-pii-ink"
						bind:value={pending.groupId}
					>
						<option value="">{$i18n.t('Select a group')}</option>
						{#each pending.targets as group (group.id)}
							<option value={group.id}>{group.name}</option>
						{/each}
					</select>
				{:else}
					<div class="mt-3 text-[12px] text-pii-muted">
						{$i18n.t('Group')}:
						<span class="text-pii-ink">{pending.targets[0]?.name ?? pending.groupId}</span>
					</div>
				{/if}

				{#if pending.mode === 'remove'}
					<label class="mt-3 block text-[12px] text-pii-muted" for="pii-policy-reason">
						{$i18n.t('Reason')}
					</label>
					<input
						id="pii-policy-reason"
						class="mt-1 w-full rounded-xl border border-pii-line bg-pii-white px-2.5 py-1.5 text-[13px] text-pii-ink"
						type="text"
						bind:value={pending.reason}
						placeholder={$i18n.t('Reason for removing enforcement (required)')}
					/>
				{/if}

				<div class="mt-4 flex justify-end gap-2">
					<Button on:click={() => (pending = null)}>{$i18n.t('Cancel')}</Button>
					<span
						class:pointer-events-none={submitting ||
							!pending.groupId ||
							(pending.mode === 'remove' && !pending.reason.trim())}
						class:opacity-40={submitting ||
							!pending.groupId ||
							(pending.mode === 'remove' && !pending.reason.trim())}
					>
						<Button variant="primary" on:click={submitAction}>
							{pending.mode === 'enforce' ? $i18n.t('Enforce') : $i18n.t('Remove')}
						</Button>
					</span>
				</div>
			</div>
		</div>
	{/if}
</div>
