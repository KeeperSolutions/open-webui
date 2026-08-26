<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { config } from '$lib/stores';
	import Analytics from '$lib/components/admin/Analytics.svelte';

	onMount(() => {
		if (!($config?.features.enable_admin_analytics ?? true)) {
			// `replaceState` for the same reason as the redirect stubs: pushing
			// would leave the blocked route in history, and Back would land on it
			// and be bounced forward again.
			goto('/admin', { replaceState: true });
		}
	});
</script>

{#if $config?.features.enable_admin_analytics ?? true}
	<Analytics />
{/if}
