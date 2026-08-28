<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
<<<<<<< HEAD
	import { onMount, getContext } from 'svelte';

	import { page } from '$app/stores';
	import { user, showSidebar, config } from '$lib/stores';
	import { getAutomationById } from '$lib/apis/automations';
=======
	import { onMount } from 'svelte';

	import { page } from '$app/stores';
	import { user, config } from '$lib/stores';
	import { getAutomationById, type AutomationResponse } from '$lib/apis/automations';
>>>>>>> v0.11.0

	import AutomationEditor from '$lib/components/automations/AutomationEditor.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

<<<<<<< HEAD
	const i18n = getContext('i18n');

	let automation = null;
=======
	let automation: AutomationResponse | null = null;
>>>>>>> v0.11.0
	let loaded = false;

	$: automationId = $page.params.id;

	onMount(async () => {
		if (
<<<<<<< HEAD
			!$config?.features?.enable_automations ||
=======
			!($config?.features as any)?.enable_automations ||
>>>>>>> v0.11.0
			($user?.role !== 'admin' && !($user?.permissions?.features?.automations ?? false))
		) {
			goto('/');
			return;
		}

		if (automationId) {
			const res = await getAutomationById(localStorage.token, automationId).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (res) {
				automation = res;
				loaded = true;
			} else {
				goto('/automations');
			}
		} else {
			goto('/automations');
		}
	});
</script>

{#if loaded && automation}
	<AutomationEditor {automation} />
{:else}
<<<<<<< HEAD
	<div
		class="w-full h-screen max-h-[100dvh] flex justify-center items-center transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: ''}"
	>
=======
	<div class="flex h-full w-full items-center justify-center">
>>>>>>> v0.11.0
		<Spinner className="size-5" />
	</div>
{/if}
