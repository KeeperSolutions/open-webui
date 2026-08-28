<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
<<<<<<< HEAD
		// A redirect, not a destination — see the note in /admin/+page.svelte.
=======
>>>>>>> v0.11.0
		goto('/admin/functions/create', { replaceState: true });
	});
</script>
