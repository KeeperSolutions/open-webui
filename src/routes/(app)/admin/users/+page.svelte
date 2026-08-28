<script>
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(async () => {
<<<<<<< HEAD
		// A redirect, not a destination — see the note in /admin/+page.svelte.
=======
>>>>>>> v0.11.0
		await goto('/admin/users/overview', { replaceState: true });
	});
</script>
