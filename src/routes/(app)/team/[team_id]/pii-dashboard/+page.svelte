<script lang="ts">
	import { page } from '$app/stores';
	import PiiDashboard from '$lib/components/admin/PiiDashboard/PiiDashboard.svelte';

	$: teamId = $page.params.team_id;
</script>

<!--
	⚠️ Keyed on `teamId`, which the `/admin` copy of this page does not need.

	SvelteKit reuses a component across a param change, so navigating from one
	team's dashboard to another would leave the three loaders bound to the team
	they were constructed with — the screen would say one team in the address and
	show another's data. Keying forces a fresh instance instead.

	Nothing here is a guard. Every check is on the route the loaders call.
-->
{#key teamId}
	<PiiDashboard {teamId} />
{/key}
