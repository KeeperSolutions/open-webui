<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, createEventDispatcher } from 'svelte';

	import { getFeaturedModels, setFeaturedModels } from '$lib/apis/configs';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import FeaturedModels from './FeaturedModels.svelte';
	import { validateFeaturedModels } from './featuredModels';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;

	let loading = false;
	let featuredModels: {
		model_id: string;
		provider_name: string;
		featured_name: string;
		tags: [string, string, string];
		order: number;
	}[] = [];

	// Provider name is mandatory (3–24 chars) for every featured entry and tags
	// are capped at 10 chars — see ./featuredModels.ts. Save is blocked while the
	// list violates any of these (mirrored by the backend validator).
	$: validationError = validateFeaturedModels(featuredModels);

	$: if (show) {
		init();
	}

	// The backend validator (routers/configs.py) rejects malformed entries on
	// save, but this reads whatever is already stored — from before that
	// validation existed, or a direct API/DB write it missed. FeaturedModels.svelte
	// binds tags by index (featuredModels[idx].tags[tagIdx]), so a non-object
	// entry or a short/missing tags array would throw there rather than just
	// showing a blank row. Coerce every loaded entry into a well-formed shape
	// (rather than dropping it, unlike the read-only chat-selector path in
	// featuredModels.ts) so the admin can see and fix whatever is wrong with it.
	const normalizeEntry = (entry: unknown, order: number) => {
		const e = (entry && typeof entry === 'object' ? entry : {}) as Record<string, unknown>;
		const rawTags = Array.isArray(e.tags) ? e.tags : [];
		const tags: [string, string, string] = [
			typeof rawTags[0] === 'string' ? rawTags[0] : '',
			typeof rawTags[1] === 'string' ? rawTags[1] : '',
			typeof rawTags[2] === 'string' ? rawTags[2] : ''
		];
		return {
			model_id: typeof e.model_id === 'string' ? e.model_id : '',
			provider_name: typeof e.provider_name === 'string' ? e.provider_name : '',
			featured_name: typeof e.featured_name === 'string' ? e.featured_name : '',
			tags,
			order: typeof e.order === 'number' ? e.order : order
		};
	};

	const init = async () => {
		try {
			const config = await getFeaturedModels(localStorage.token);
			const raw = config?.FEATURED_MODELS;
			featuredModels = Array.isArray(raw) ? raw.map(normalizeEntry) : [];
		} catch (error: unknown) {
			toast.error($i18n.t('Failed to load configuration'));
			featuredModels = [];
		}
	};

	const submitHandler = async () => {
		if (validationError) {
			toast.error($i18n.t(validationError));
			return;
		}
		loading = true;
		try {
			// setFeaturedModels writes only the featured list — it can't be
			// affected by, or affect, any other config field. See its comment
			// in $lib/apis/configs for why that matters here.
			const res = await setFeaturedModels(localStorage.token, featuredModels);

			if (res) {
				toast.success($i18n.t('Featured models saved successfully'));
				dispatch('save');
				show = false;
			} else {
				toast.error($i18n.t('Failed to save featured models'));
			}
		} catch (error: unknown) {
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
			<button class="self-center" aria-label={$i18n.t('Close')} on:click={closeModal}>
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
		<div class="flex items-center justify-between gap-2 px-5 py-3">
			<p class="text-xs text-red-500 {validationError ? '' : 'invisible'}">
				{validationError ? $i18n.t(validationError) : ''}
			</p>
			<div class="flex shrink-0 gap-2">
				<button
					class="px-3.5 py-1.5 text-sm font-medium dark:bg-black dark:hover:bg-gray-950 dark:text-white bg-white text-black hover:bg-gray-100 transition rounded-full"
					type="button"
					disabled={loading}
					on:click={closeModal}
				>
					{$i18n.t('Cancel')}
				</button>

				<button
					class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-1.5 {loading ||
					validationError
						? 'cursor-not-allowed opacity-70'
						: ''}"
					type="button"
					disabled={loading || !!validationError}
					on:click={submitHandler}
				>
					{$i18n.t('Save')}
					{#if loading}
						<Spinner className="size-4" />
					{/if}
				</button>
			</div>
		</div>
	</div>
</Modal>
