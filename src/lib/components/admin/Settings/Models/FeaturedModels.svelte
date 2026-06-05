<script lang="ts">
import Sortable from 'sortablejs';
import { getContext, onMount, onDestroy } from 'svelte';
import { models } from '$lib/stores';
import EllipsisVertical from '$lib/components/icons/EllipsisVertical.svelte';
import XMark from '$lib/components/icons/XMark.svelte';

const i18n = getContext('i18n');

export let featuredModels: {
    model_id: string;
    provider_name: string;
    featured_name: string;
    tags: [string, string, string];
    order: number;
}[] = [];

let selectedModelId = '';
let listElement: HTMLElement;
let sortable: Sortable | null = null;
let prevLength = -1;

// IDs already featured — used to exclude from picker
$: featuredIds = featuredModels.map((m) => m.model_id);
$: availableModels = $models.filter((m) => !featuredIds.includes(m.id));

// Re-init sortable only when items are added or removed, not on field edits
$: if (featuredModels.length === 0 && prevLength !== 0) {
    prevLength = 0;
    sortable?.destroy();
    sortable = null;
} else if (listElement && featuredModels.length > 0 && featuredModels.length !== prevLength) {
    prevLength = featuredModels.length;
    initSortable();
}

const addModel = () => {
    if (!selectedModelId || featuredIds.includes(selectedModelId)) {
        selectedModelId = '';
        return;
    }
    const model = $models.find((m) => m.id === selectedModelId);
    featuredModels = [
        ...featuredModels,
        {
            model_id: selectedModelId,
            provider_name: '',
            featured_name: model?.name ?? selectedModelId,
            tags: ['', '', ''],
            order: featuredModels.length
        }
    ];
    selectedModelId = '';
};

const removeModel = (idx: number) => {
    const next = featuredModels
        .filter((_, i) => i !== idx)
        .map((m, i) => ({ ...m, order: i }));
    if (next.length === 0) {
        sortable?.destroy();
        sortable = null;
    }
    featuredModels = next;
};

const reorderModels = (oldIndex: number, newIndex: number) => {
    const reordered = [...featuredModels];
    const [moved] = reordered.splice(oldIndex, 1);
    reordered.splice(newIndex, 0, moved);
    featuredModels = reordered.map((m, i) => ({ ...m, order: i }));
};

const initSortable = () => {
    sortable?.destroy();
    if (listElement && featuredModels.length > 1) {
        sortable = new Sortable(listElement, {
            animation: 150,
            handle: '.featured-drag-handle',
            onEnd: (evt) => {
                if (evt.oldIndex !== undefined && evt.newIndex !== undefined) {
                    reorderModels(evt.oldIndex, evt.newIndex);
                }
            }
        });
    }
};

onMount(() => {
    if (listElement) initSortable();
});

onDestroy(() => {
    sortable?.destroy();
});
</script>

<div class="flex flex-col w-full gap-3">
    <!-- Add model picker -->
    <div class="flex items-center gap-2">
        <select
            class="dark:bg-gray-900 w-full py-1 text-sm rounded-lg bg-transparent {selectedModelId
                ? ''
                : 'text-gray-500'} focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-600"
            bind:value={selectedModelId}
            on:change={addModel}
        >
            <option value="">{$i18n.t('Add a featured model…')}</option>
            {#each availableModels as model}
                <option value={model.id} class="bg-gray-50 dark:bg-gray-700">{model.name}</option>
            {/each}
        </select>
    </div>

    <!-- Featured model list -->
    {#if featuredModels.length > 0}
        <div class="flex flex-col gap-2" bind:this={listElement}>
            {#each featuredModels as entry, idx (entry.model_id)}
                <div
                    data-model-id={entry.model_id}
                    class="flex flex-col gap-2 rounded-xl border border-gray-100 dark:border-gray-700 p-3"
                >
                    <!-- Row header -->
                    <div class="flex items-center justify-between gap-2">
                        <div class="flex items-center gap-1.5 min-w-0">
                            <span
                                class="featured-drag-handle cursor-move text-gray-400 dark:text-gray-500"
                                title={$i18n.t('Drag to reorder')}
                            >
                                <EllipsisVertical className="size-4" />
                            </span>
                            <span class="text-sm font-medium truncate text-gray-700 dark:text-gray-200">
                                {$models.find((m) => m.id === entry.model_id)?.name ?? entry.model_id}
                            </span>
                        </div>
                        <button
                            type="button"
                            class="shrink-0 text-gray-400 hover:text-red-500 transition"
                            aria-label={$i18n.t('Remove')}
                            on:click={() => removeModel(idx)}
                        >
                            <XMark className="size-4" />
                        </button>
                    </div>

                    <!-- Fields -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        <div class="flex flex-col gap-0.5">
                            <label class="text-xs text-gray-500">{$i18n.t('Provider Name')}</label>
                            <input
                                type="text"
                                class="text-sm rounded-lg px-2.5 py-1.5 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-600 w-full"
                                placeholder={$i18n.t('e.g. OpenAI')}
                                bind:value={featuredModels[idx].provider_name}
                            />
                        </div>
                        <div class="flex flex-col gap-0.5">
                            <label class="text-xs text-gray-500">{$i18n.t('Featured Name')}</label>
                            <input
                                type="text"
                                class="text-sm rounded-lg px-2.5 py-1.5 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-600 w-full"
                                placeholder={$i18n.t('Display name')}
                                bind:value={featuredModels[idx].featured_name}
                            />
                        </div>
                    </div>

                    <!-- Tags -->
                    <div class="grid grid-cols-3 gap-2">
                        {#each [0, 1, 2] as tagIdx}
                            <div class="flex flex-col gap-0.5">
                                <label class="text-xs text-gray-500">{$i18n.t('Tag {{n}}', { n: tagIdx + 1 })}</label>
                                <input
                                    type="text"
                                    maxlength="20"
                                    class="text-sm rounded-lg px-2.5 py-1.5 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-600 w-full"
                                    placeholder={$i18n.t('Short tag')}
                                    bind:value={featuredModels[idx].tags[tagIdx]}
                                />
                            </div>
                        {/each}
                    </div>
                </div>
            {/each}
        </div>
    {:else}
        <div class="text-gray-400 dark:text-gray-500 text-xs text-center py-4">
            {$i18n.t('No featured models added yet.')}
        </div>
    {/if}
</div>