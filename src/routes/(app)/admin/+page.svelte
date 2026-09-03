<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
		// `replaceState` because this route is a redirect, not a destination.
		// Pushing leaves it in the history stack, and going Back lands on it —
		// where this same onMount fires and pushes forward again, so Back can
		// never leave the admin area.
		goto('/admin/users', { replaceState: true });
	});
</script>
