<script lang="ts">
	import { onMount, getContext, tick } from 'svelte';
	// @ts-ignore - sortablejs has no bundled types in this project (used the same way elsewhere)
	import Sortable from 'sortablejs';
	import { toast } from 'svelte-sonner';

	import {
		getModelClasses,
		createModelClass,
		updateModelClass,
		deleteModelClass,
		reorderModelClasses
	} from '$lib/apis/model-classes';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import EllipsisVertical from '$lib/components/icons/EllipsisVertical.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Pencil from '$lib/components/icons/Pencil.svelte';

	const i18n = getContext('i18n');

	let modelClasses: any[] = [];
	let loading = true;

	let showDeleteConfirmDialog = false;
	let deleteId: number | null = null;

	let tbodyElement: HTMLTableSectionElement | null = null;
	let sortable: Sortable | null = null;

	$: maxCreditBurn = modelClasses.length
		? Math.max(...modelClasses.map((c) => c.credit_burn || 0))
		: 1;

	const TIER = {
		pro: 1300,
		premium: 3800,
		business: 1000
	};

	const getMsgs = (burn: number, credit: number) =>
		burn > 0 ? Math.round(credit / burn) : 0;

	const getMeterWidth = (burn: number) =>
		maxCreditBurn > 0 ? Math.min(100, Math.round((burn / maxCreditBurn) * 100)) : 0;

	// Inline editing state
	let editingId: number | null = null;
	let editForm: any = null;
	let isAdding = false;

	function startAdd() {
		if (editingId !== null || isAdding) return;
		isAdding = true;
		editingId = -1;
		editForm = {
			name: '',
			models: '',
			credit_burn: null,
			msgs_pro: null,
			msgs_premium: null,
			msgs_business: null
		};
		if (sortable) {
			sortable.destroy();
			sortable = null;
		}
	}

	function startEdit(row: any) {
		if (isAdding || editingId !== null) return;
		editingId = row.id;
		editForm = {
			name: row.name || '',
			models: (row.models || []).join(', '),
			credit_burn: row.credit_burn || 0,
			msgs_pro: row.msgs_pro || '',
			msgs_premium: row.msgs_premium || '',
			msgs_business: row.msgs_business || ''
		};
		if (sortable) {
			sortable.destroy();
			sortable = null;
		}
	}

	function cancelEdit() {
		editingId = null;
		editForm = null;
		isAdding = false;
		tick().then(initSortable);
	}

	async function saveEdit() {
		if (!editForm) return;

		const modelsArr = editForm.models
			? editForm.models
					.split(',')
					.map((m: string) => m.trim())
					.filter((m: string) => m.length > 0)
			: null;

		const payload = {
			name: editForm.name,
			models: modelsArr,
			credit_burn: editForm.credit_burn != null ? Number(editForm.credit_burn) : 0,
			msgs_pro: editForm.msgs_pro != null ? String(editForm.msgs_pro) : null,
			msgs_premium: editForm.msgs_premium != null ? String(editForm.msgs_premium) : null,
			msgs_business: editForm.msgs_business != null ? String(editForm.msgs_business) : null,
			order: null
		};

		try {
			if (isAdding) {
				await createModelClass(localStorage.token, payload);
				toast.success($i18n.t('Model class created'));
			} else if (editingId !== null && editingId > 0) {
				await updateModelClass(localStorage.token, editingId, payload);
				toast.success($i18n.t('Model class updated'));
			}
			await loadModelClasses();
			cancelEdit();
		} catch (error: any) {
			const msg = error?.detail || error?.message || $i18n.t('Failed to save');
			toast.error(msg);
		}
	}

	onMount(() => {
		loadModelClasses();
	});

	const loadModelClasses = async () => {
		loading = true;
		try {
			modelClasses = await getModelClasses(localStorage.token);
		} catch (error: any) {
			toast.error(error?.detail || $i18n.t('Failed to load model classes'));
		} finally {
			loading = false;
			tick().then(initSortable);
		}
	};

	const initSortable = () => {
		if (sortable) {
			sortable.destroy();
			sortable = null;
		}

		if (tbodyElement && modelClasses.length > 1) {
			sortable = new Sortable(tbodyElement, {
				animation: 150,
				handle: '.drag-handle',
				onEnd: async (evt) => {
					if (evt.oldIndex === undefined || evt.newIndex === undefined) return;

					const reordered = [...modelClasses];
					const [moved] = reordered.splice(evt.oldIndex, 1);
					reordered.splice(evt.newIndex, 0, moved);

					// Update local order for immediate UI feedback
					modelClasses = reordered.map((m, index) => ({ ...m, order: index + 1 }));

					const reorderPayload = modelClasses.map((m) => ({
						id: m.id,
						order: m.order
					}));

					try {
						await reorderModelClasses(localStorage.token, reorderPayload);
					} catch (error) {
						toast.error($i18n.t('Failed to save order'));
						// Reload to get correct server state
						await loadModelClasses();
					}
				}
			});
		}
	};



	const confirmDelete = (id: number) => {
		deleteId = id;
		showDeleteConfirmDialog = true;
	};

	const deleteModelClassHandler = async () => {
		if (deleteId === null) return;

		try {
			await deleteModelClass(localStorage.token, deleteId);
			toast.success($i18n.t('Model class deleted'));
			await loadModelClasses();
		} catch (error) {
			toast.error($i18n.t('Failed to delete model class'));
		} finally {
			showDeleteConfirmDialog = false;
			deleteId = null;
		}
	};
</script>

<div class="flex flex-col h-full text-sm">
	<div class="flex justify-between items-center mb-3">
		<div class="text-sm font-medium">{$i18n.t('Model Classes')}</div>
		{#if editingId === null && !isAdding}
			<button
				class="px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 rounded-lg transition flex items-center gap-1"
				on:click={startAdd}
			>
				<Plus className="size-3" />
				{$i18n.t('Add Model Class')}
			</button>
		{/if}
	</div>

	<div class="flex-1 overflow-y-auto pr-1.5">
		{#if loading}
			<div class="flex justify-center py-8">
				<div class="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900 dark:border-white"></div>
			</div>
		{:else}
			{#if modelClasses.length === 0 && !isAdding}
				<div class="text-center py-8 text-gray-500 dark:text-gray-400">
					{$i18n.t('No model classes configured')}
				</div>
			{/if}
			<div class="overflow-x-auto">
				<table class="w-full text-sm text-left text-gray-500 dark:text-gray-400 table-auto">
					<thead class="text-xs text-gray-800 uppercase bg-transparent dark:text-gray-200">
						<tr class="border-b-[1.5px] border-gray-50 dark:border-gray-850/30">
							<th class="px-1 py-2 w-5"></th>
							<th class="px-3 py-2">Model class</th>
							<th class="px-3 py-2">Credit burn / message</th>
							<th class="px-3 py-2 text-right">Pro</th>
							<th class="px-3 py-2 text-right">Premium</th>
							<th class="px-3 py-2 text-right">Business</th>
							<th class="px-2 py-2 w-10"></th>
						</tr>
					</thead>
					<tbody bind:this={tbodyElement}>
						<!-- Inline "Add new" row -->
						{#if isAdding && editingId === -1}
							<tr class="border-b border-gray-50 dark:border-gray-850/20 bg-gray-50 dark:bg-gray-800">
								<td class="px-1 py-2"></td>
								<td class="px-3 py-2">
									<input
										type="text"
										bind:value={editForm.name}
										placeholder="Name"
										class="w-32 text-sm bg-transparent border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none"
										on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
									/>
									<input
										type="text"
										bind:value={editForm.models}
										placeholder="model-1, model-2"
										class="block w-48 text-xs mt-1 bg-transparent border border-gray-200 dark:border-gray-700 rounded px-2 py-0.5 focus:outline-none"
									/>
								</td>
								<td class="px-3 py-2">
									<input
										type="number"
										step="0.1"
										bind:value={editForm.credit_burn}
										placeholder="0"
										class="w-20 text-sm font-mono bg-transparent border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none"
										on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
									/>
								</td>
								<td class="px-3 py-2 text-right">
									<input
										type="number"
										step="1"
										min="0"
										bind:value={editForm.msgs_pro}
										placeholder="—"
										class="w-20 text-sm font-mono bg-transparent border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none text-right"
										on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
									/>
									<small class="block text-gray-400">msgs / month</small>
								</td>
								<td class="px-3 py-2 text-right">
									<input
										type="number"
										step="1"
										min="0"
										bind:value={editForm.msgs_premium}
										placeholder="—"
										class="w-20 text-sm font-mono bg-transparent border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none text-right"
										on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
									/>
									<small class="block text-gray-400">≈ / day</small>
								</td>
								<td class="px-3 py-2 text-right">
									<input
										type="number"
										step="1"
										min="0"
										bind:value={editForm.msgs_business}
										placeholder="—"
										class="w-20 text-sm font-mono bg-transparent border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none text-right"
										on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
									/>
									<small class="block text-gray-400">msgs / seat</small>
								</td>
								<td class="px-2 py-2 text-right">
									<div class="flex gap-1 justify-end">
										<button
											class="px-2 py-0.5 text-xs bg-black text-white dark:bg-white dark:text-black rounded"
											on:click={saveEdit}
										>
											Save
										</button>
										<button
											class="px-2 py-0.5 text-xs bg-gray-200 dark:bg-gray-700 rounded"
											on:click={cancelEdit}
										>
											Cancel
										</button>
									</div>
								</td>
							</tr>
						{/if}

						{#each modelClasses as modelClass (modelClass.id)}
							{@const burn = modelClass.credit_burn || 0}
							{@const proMsgs = modelClass.msgs_pro || getMsgs(burn, TIER.pro)}
							{@const premMsgs = modelClass.msgs_premium || getMsgs(burn, TIER.premium)}
							{@const busMsgs = modelClass.msgs_business || getMsgs(burn, TIER.business)}
							{@const meterWidth = getMeterWidth(burn)}
							{@const isEditing = editingId === modelClass.id}

							<tr class="border-b border-gray-50 dark:border-gray-850/20 hover:bg-gray-50 dark:hover:bg-gray-850/50 {isEditing ? 'bg-gray-50 dark:bg-gray-800' : ''}">
								<td class="px-1 py-2 {isEditing ? '' : 'cursor-move'} align-top">
									{#if !isEditing}
										<EllipsisVertical className="size-4 drag-handle text-gray-400 dark:text-gray-600" />
									{/if}
								</td>

								<td class="px-3 py-2 align-top">
									{#if isEditing}
										<input
											type="text"
											bind:value={editForm.name}
											class="w-32 text-sm bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none"
											on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
										/>
										<input
											type="text"
											bind:value={editForm.models}
											placeholder="models"
											class="block w-48 text-xs mt-1 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded px-2 py-0.5 focus:outline-none"
										/>
									{:else}
										<div class="font-medium text-gray-900 dark:text-white">{modelClass.name}</div>
										{#if modelClass.models && modelClass.models.length > 0}
											<small class="text-gray-500 dark:text-gray-400">
												{modelClass.models.join(' · ')}
											</small>
										{/if}
									{/if}
								</td>

								<td class="px-3 py-2 align-top">
									{#if isEditing}
										<input
											type="number"
											step="0.1"
											bind:value={editForm.credit_burn}
											class="w-20 text-sm font-mono bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none"
											on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
										/>
									{:else}
										<div class="flex items-center gap-2">
											<span class="font-mono text-sm">{burn}</span>
											<div class="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded min-w-[60px]">
												<div
													class="h-1.5 bg-gray-900 dark:bg-white rounded transition-all"
													style="width: {meterWidth}%"
												></div>
											</div>
										</div>
									{/if}
								</td>

								<td class="px-3 py-2 text-right align-top">
									{#if isEditing}
										<input
											type="number"
											step="1"
											min="0"
											bind:value={editForm.msgs_pro}
											placeholder="~{proMsgs}"
											class="w-20 text-sm font-mono bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none text-right"
											on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
										/>
										<small class="block text-gray-400">msgs / month</small>
									{:else}
										<div>
											<span class="font-mono text-sm">~{proMsgs}</span>
											<small class="block text-gray-500 dark:text-gray-400">msgs / month</small>
										</div>
									{/if}
								</td>

								<td class="px-3 py-2 text-right align-top">
									{#if isEditing}
										<input
											type="number"
											step="1"
											min="0"
											bind:value={editForm.msgs_premium}
											placeholder="~{premMsgs}"
											class="w-20 text-sm font-mono bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none text-right"
											on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
										/>
										<small class="block text-gray-400">≈ {Math.round(premMsgs / 22)} / day</small>
									{:else}
										<div>
											<span class="font-mono text-sm">~{premMsgs}</span>
											<small class="block text-gray-500 dark:text-gray-400">≈ {Math.round(premMsgs / 22)} / working day</small>
										</div>
									{/if}
								</td>

								<td class="px-3 py-2 text-right align-top">
									{#if isEditing}
										<input
											type="number"
											step="1"
											min="0"
											bind:value={editForm.msgs_business}
											placeholder="~{busMsgs}"
											class="w-20 text-sm font-mono bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none text-right"
											on:keydown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
										/>
										<small class="block text-gray-400">msgs / seat</small>
									{:else}
										<div>
											<span class="font-mono text-sm">~{busMsgs}</span>
											<small class="block text-gray-500 dark:text-gray-400">msgs / seat</small>
										</div>
									{/if}
								</td>

								<td class="px-2 py-2 text-right align-top">
									{#if isEditing}
										<div class="flex gap-1 justify-end">
											<button
												class="px-2 py-0.5 text-xs bg-black text-white dark:bg-white dark:text-black rounded"
												on:click={saveEdit}
											>
												Save
											</button>
											<button
												class="px-2 py-0.5 text-xs bg-gray-200 dark:bg-gray-700 rounded"
												on:click={cancelEdit}
											>
												Cancel
											</button>
										</div>
									{:else}
										<div class="flex items-center justify-end gap-1">
											<button
												class="p-1 text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 rounded transition"
												on:click={() => startEdit(modelClass)}
												title={$i18n.t('Edit')}
											>
												<Pencil className="size-3.5" />
											</button>
											<button
												class="p-1 text-gray-400 hover:text-red-600 dark:hover:text-red-400 rounded transition"
												on:click={() => confirmDelete(modelClass.id)}
												title={$i18n.t('Delete')}
											>
												<svg xmlns="http://www.w3.org/2000/svg" class="size-4" viewBox="0 0 20 20" fill="currentColor">
													<path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" />
												</svg>
											</button>
										</div>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	on:confirm={deleteModelClassHandler}
/>
