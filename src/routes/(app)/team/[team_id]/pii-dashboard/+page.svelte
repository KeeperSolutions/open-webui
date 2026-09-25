<script lang="ts">
	import { page } from '$app/stores';
	import { showSidebar } from '$lib/stores';
	import PiiDashboard from '$lib/components/admin/PiiDashboard/PiiDashboard.svelte';

	$: teamId = $page.params.team_id;
</script>

<!--
	Page container. `(app)/+layout.svelte` renders only the sidebar and a bare
	`<slot />`, and this route has no layout of its own to supply one.

	The wrapper needs both `flex-1` and the sidebar-width `max-w` clamp on the same
	element. Without `flex-1` the dashboard shrinks to its content and leaves an
	empty band beside the sidebar. With `flex-1` alone it spans the viewport and
	slides under the out-of-flow `#sidebar`. The clamps, including the 42px
	collapsed rail, are copied from `admin/+layout.svelte`.

	It lives here rather than on `PiiDashboard`'s root because the admin route
	already wraps the dashboard in the same container.
-->
<div
	class="flex flex-col h-screen max-h-[100dvh] flex-1 min-w-0 transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: 'md:max-w-[calc(100%-42px)]'} w-full max-w-full"
>
	<div class="pb-1 flex-1 min-w-0 max-h-full overflow-y-auto overflow-x-hidden">
		<!--
			Keyed on `teamId` because SvelteKit reuses the component across a param
			change, which would leave the loaders bound to the previous team and show
			its data under the new address. This is not a guard: access is checked by
			the routes the loaders call.
		-->
		{#key teamId}
			<PiiDashboard {teamId} />
		{/key}
	</div>
</div>
