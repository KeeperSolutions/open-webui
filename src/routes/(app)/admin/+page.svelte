<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
<<<<<<< HEAD
		// `replaceState` because this route is a redirect, not a destination.
		// Pushing leaves it in the history stack, and going Back lands on it —
		// where this same onMount fires and pushes forward again, so Back can
		// never leave the admin area.
=======
>>>>>>> v0.11.0
		goto('/admin/users', { replaceState: true });
	});
</script>
