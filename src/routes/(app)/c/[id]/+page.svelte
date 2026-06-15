<script lang="ts">
	import { page } from '$app/stores';

	import Chat from '$lib/components/chat/Chat.svelte';
	import ChatBackground from '$lib/components/chat/ChatBackground.svelte';
</script>

<ChatBackground />
<Chat chatIdProp={$page.params.id} />
