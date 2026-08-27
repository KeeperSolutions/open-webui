<script lang="ts">
	import { page } from '$app/stores';
	import { showSidebar } from '$lib/stores';
	import PiiDashboard from '$lib/components/admin/PiiDashboard/PiiDashboard.svelte';

	$: teamId = $page.params.team_id;
</script>

<!--
	⚠️ The filling wrapper this route has to supply for itself.

	`(app)/+layout.svelte` renders the sidebar and then a bare `<slot />`. Every
	page under it brings its own container: the chat page has one, and
	`/admin/pii-dashboard` inherits one from `admin/+layout.svelte`. This route has
	no layout of its own, so nothing was giving it one, and the dashboard sized
	itself to its content — capped at `max-w-[1190px]` — while the row's
	`justify-content: flex-end` pushed that block against the right edge. The
	surplus showed as an empty dark band between the sidebar and the screen, and it
	grew with the window. Measured at 1200px: left edge 298 instead of 260.

	⚠️ `flex-1` alone is NOT the fix, and the first attempt proved it: `#sidebar` is
	out of flow, so a stretched item spans the whole viewport and slides UNDER it —
	measured left edge 1, with the sidebar ending at 260. The width has to be
	reserved explicitly, which is what the `max-w` clamp below does. It is copied
	from `admin/+layout.svelte:31-33` rather than reinvented, including the 49px
	rail for the collapsed state.

	Deliberately here rather than on `PiiDashboard`'s own root: the admin copy is
	already inside such a container, and widening the shared component would change
	a screen this fix has no business touching.
-->
<div
	class="flex flex-col h-screen max-h-[100dvh] flex-1 transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: 'md:max-w-[calc(100%-49px)]'} w-full max-w-full"
>
	<div class="pb-1 flex-1 max-h-full overflow-y-auto">
		<!--
			⚠️ Keyed on `teamId`, which the `/admin` copy of this page does not need.

			SvelteKit reuses a component across a param change, so navigating from one
			team's dashboard to another would leave the three loaders bound to the team
			they were constructed with — the screen would say one team in the address
			and show another's data. Keying forces a fresh instance instead.

			Nothing here is a guard. Every check is on the route the loaders call.
		-->
		{#key teamId}
			<PiiDashboard {teamId} />
		{/key}
	</div>
</div>
