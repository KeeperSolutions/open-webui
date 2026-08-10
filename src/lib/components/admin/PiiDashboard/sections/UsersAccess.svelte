<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { goto } from '$app/navigation';
	import SectionHeader from '../parts/SectionHeader.svelte';
	import Pill from '../parts/Pill.svelte';
	import Toggle from '../parts/Toggle.svelte';
	import Button from '../parts/Button.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import type { MetricRow } from '$lib/apis/langfuse';
	import { formatCostDisplay } from '$lib/apis/langfuse/tableUtils';
	import {
		buildRows,
		unattributedCost,
		type AccessUser,
		type UserRow,
		type UserStatus
	} from './usersAccess';
	import { grantedModelIds, type ModelRecord } from '../modelAccess';
	import type { Truncation } from '../usersAccessLoader';

	const i18n: Writable<i18nType> = getContext('i18n');

	/** The directory. Without it there is no table, so it drives loading/failed. */
	export let users: AccessUser[] = [];
	/** Spend for the selected window. A second, slower source — see `costUnknown`. */
	export let metricRows: MetricRow[] = [];
	export let truncated: Truncation | null = null;
	/** The registered catalogue this section measures access against. */
	export let models: ModelRecord[] = [];
	export let truncatedModels: Truncation | null = null;
	export let loading = true;
	export let failed = false;
	export let errorDetail: string | null = null;
	export let onRetry: () => void;
	/** Spend has never arrived, or its last fetch failed: show it as unknown. */
	export let costUnknown = false;
	/** Spend on screen belongs to the previous window; a newer one is in flight. */
	export let costStale = false;

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
					primary = Number(a.maskingEnabled) - Number(b.maskingEnabled);
					break;
				case 'cost':
					primary = a.cost - b.cost;
					break;
			}
			// Ties resolve by name so the order does not shuffle between renders.
			return primary !== 0 ? primary * sign : a.name.localeCompare(b.name);
		});
	};

	$: rows = buildRows(users, metricRows, { models, truncated: !!truncatedModels });

	/**
	 * The full granted list per user, for the cell's `title`. Kept out of UserRow
	 * because only the hover text needs it.
	 */
	$: grantedNames = new Map(
		users.map((u) => [u.id, [...grantedModelIds(u, models)].sort().join(', ')])
	);
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

	// `key: null` marks a column that is not sortable: the value shown is a
	// derived label, and ordering by the number behind it while the label reads
	// "All models" would look arbitrary.
	const COLUMNS: { key: SortKey | null; label: string }[] = [
		{ key: 'name', label: 'User' },
		{ key: 'role', label: 'Role' },
		{ key: 'status', label: 'Status' },
		{ key: null, label: 'Models granted' },
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

			{#if truncatedModels}
				<div
					class="rounded-xl border border-pii-line bg-pii-side px-3.5 py-2.5 text-[12px] text-pii-muted"
				>
					{$i18n.t(
						'Model catalogue truncated at {{shown}} of {{total}} — "All models" is not shown while the list is incomplete.',
						{ shown: truncatedModels.shown, total: truncatedModels.total }
					)}
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
							<th scope="col" class="px-2.5 py-2"></th>
						</tr>
					</thead>
					<tbody>
						{#each sorted as row (row.id)}
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
								<td
									class="px-2.5 py-2.5 align-middle"
									title={grantedNames.get(row.id) || undefined}
								>
									{#if row.allModels}
										<span class="text-pii-ink">{$i18n.t('All models')}</span>
									{:else if row.grantedCount > 0}
										<!-- Two keys rather than i18next plurals: the en-US catalogue carries
										     empty values, so `_one`/`_other` resolve back to the base key and
										     a single grant reads "1 models". -->
										<span class="text-pii-ink"
											>{row.grantedCount === 1
												? $i18n.t('1 model')
												: $i18n.t('{{count}} models', { count: row.grantedCount })}</span
										>
									{:else}
										<span class="text-pii-muted">—</span>
									{/if}
								</td>
								<td class="px-2.5 py-2.5 align-middle">
									<div class="flex items-center gap-2">
										<Toggle
											on={row.maskingEnabled}
											disabled
											ariaLabel={$i18n.t('PII masking for {{name}}', { name: row.name })}
										/>
										{#if row.maskingEnabled}
											<span class="text-pii-ink">{$i18n.t('On')}</span>
										{:else}
											<Pill kind="warn">{$i18n.t('Off — flagged')}</Pill>
										{/if}
									</div>
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
								<td class="px-2.5 py-2.5 align-middle text-pii-muted">—</td>
								<td class="px-2.5 py-2.5 align-middle text-pii-muted">—</td>
								<td class="px-2.5 py-2.5 align-middle font-bold text-pii-ink">
									{formatCostDisplay(unattributed)}
								</td>
								<!-- No Manage button: there is no account to manage. -->
								<td class="px-2.5 py-2.5"></td>
							</tr>
						</tfoot>
					{/if}
				</table>
			</div>
		</div>
	{/if}
</div>
