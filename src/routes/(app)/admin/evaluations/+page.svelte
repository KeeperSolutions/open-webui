<script>
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	import Evaluations from '$lib/components/admin/Evaluations.svelte';

	onMount(() => {
		// A redirect, not a destination — see the note in /admin/+page.svelte.
		goto('/admin/evaluations/leaderboard', { replaceState: true });
	});
</script>

<Evaluations />
