<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
		// A redirect, not a destination — see the note in /admin/+page.svelte.
		goto('/admin/functions/create', { replaceState: true });
	});
</script>
