<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Chat from '$lib/components/chat/Chat.svelte';
	import { page } from '$app/stores';

	onMount(() => {
		if ($page.url.searchParams.get('error')) {
			toast.error($page.url.searchParams.get('error') || 'An unknown error occurred.');
		}
	});
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
<Chat />
