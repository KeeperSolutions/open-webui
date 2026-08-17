<script>
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(async () => {
		// A redirect, not a destination — see the note in /admin/+page.svelte.
		await goto('/admin/users/overview', { replaceState: true });
	});
</script>
