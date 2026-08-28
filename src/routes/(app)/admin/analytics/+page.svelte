<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { config } from '$lib/stores';

	onMount(() => {
		if (!($config?.features.enable_admin_analytics ?? true)) {
<<<<<<< HEAD
			// `replaceState` for the same reason as the redirect stubs: pushing
			// would leave the blocked route in history, and Back would land on it
			// and be bounced forward again.
			goto('/admin', { replaceState: true });
=======
			goto('/admin', { replaceState: true });
		} else {
			goto('/?settings=admin%3Aanalytics', { replaceState: true });
>>>>>>> v0.11.0
		}
	});
</script>
