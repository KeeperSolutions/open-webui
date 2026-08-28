<script>
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
<<<<<<< HEAD
		// A redirect, not a destination — see the note in /admin/+page.svelte.
		goto('/admin/settings/general', { replaceState: true });
=======
		goto('/?settings=admin%3Ageneral', { replaceState: true });
>>>>>>> v0.11.0
	});
</script>
