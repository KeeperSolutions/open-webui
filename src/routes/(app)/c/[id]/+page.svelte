<script lang="ts">
	import { page } from '$app/stores';

	import Chat from '$lib/components/chat/Chat.svelte';
</script>

<img
	src="/hubgate/hubgate-pixel-pattern.svg"
	alt=""
	aria-hidden="true"
	class="pointer-events-none dark:hidden"
	style="position:fixed;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:0"
/>
<img
	src="/hubgate/hubgate-pixel-pattern-dark.svg"
	alt=""
	aria-hidden="true"
	class="pointer-events-none hidden dark:block"
	style="position:fixed;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:0"
/>
<div class="relative" style="z-index:1">
	<Chat chatIdProp={$page.params.id} />
</div>
