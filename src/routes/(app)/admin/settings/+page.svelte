<script>
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import Settings from '$lib/components/admin/Settings.svelte';

	onMount(() => {
		// A redirect, not a destination — see the note in /admin/+page.svelte.
		goto('/admin/settings/general', { replaceState: true });
	});
</script>

<Settings />
