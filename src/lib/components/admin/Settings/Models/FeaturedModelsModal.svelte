<script lang="ts">
    import { toast } from 'svelte-sonner';
    import { getContext, createEventDispatcher } from 'svelte';

    import { getModelsConfig, setModelsConfig } from '$lib/apis/configs';
    import Modal from '$lib/components/common/Modal.svelte';
    import Spinner from '$lib/components/common/Spinner.svelte';
    import XMark from '$lib/components/icons/XMark.svelte';
    import FeaturedModels from './FeaturedModels.svelte';

    const i18n = getContext('i18n');
    const dispatch = createEventDispatcher();

    export let show = false;

    let loading = false;
    let existingConfig: Record<string, any> = {};
    let featuredModels: {
        model_id: string;
        provider_name: string;
        featured_name: string;
        tags: [string, string, string];
        order: number;
    }[] = [];

    $: if (show) {
        init();
    }

    const init = async () => {
        try {
            existingConfig = (await getModelsConfig(localStorage.token)) ?? {};
            const raw = existingConfig?.FEATURED_MODELS;
            featuredModels = Array.isArray(raw) ? [...raw] : [];
        } catch (error) {
            toast.error($i18n.t('Failed to load configuration'));
            featuredModels = [];
        }
    };

    const submitHandler = async () => {
        loading = true;
        try {
            const res = await setModelsConfig(localStorage.token, {
                ...existingConfig,
                FEATURED_MODELS: featuredModels
            });

            if (res) {
                toast.success($i18n.t('Featured models saved successfully'));
                dispatch('save');
                show = false;
            } else {
                toast.error($i18n.t('Failed to save featured models'));
            }
        } catch (error) {
            toast.error($i18n.t('Failed to save featured models'));
        } finally {
            loading = false;
        }
    };

    const closeModal = () => {
        show = false;
    };
</script>

<Modal size="md" bind:show>
    <div>
        <!-- Header -->
        <div class="flex justify-between items-center dark:text-gray-100 px-5 pt-4 pb-2">
            <div class="text-lg font-medium font-primary">
                {$i18n.t('Featured Models')}
            </div>
            <button
                class="self-center"
                aria-label={$i18n.t('Close')}
                on:click={closeModal}
            >
                <XMark className="size-5" />
            </button>
        </div>

        <!-- Body -->
        <div class="px-5 pb-2 dark:text-gray-200 max-h-[70vh] overflow-y-auto">
            <p class="text-xs text-gray-500 mb-3">
                {$i18n.t(
                    'Featured models are shown at the top of the model selector. Set a display name, provider, and up to 3 short tags per model.'
                )}
            </p>
            <FeaturedModels bind:featuredModels />
        </div>

        <!-- Footer -->
        <div class="flex justify-end gap-2 px-5 py-3">
            <button
                class="px-3.5 py-1.5 text-sm font-medium dark:bg-black dark:hover:bg-gray-950 dark:text-white bg-white text-black hover:bg-gray-100 transition rounded-full"
                type="button"
                disabled={loading}
                on:click={closeModal}
            >
                {$i18n.t('Cancel')}
            </button>

            <button
                class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-1.5 {loading
                    ? 'cursor-not-allowed opacity-70'
                    : ''}"
                type="button"
                disabled={loading}
                on:click={submitHandler}
            >
                {$i18n.t('Save')}
                {#if loading}
                    <Spinner className="size-4" />
                {/if}
            </button>
        </div>
    </div>
</Modal>