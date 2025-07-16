<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	const i18n = getContext('i18n');
	import FileExplorer from '$lib/components/file-explorer/FileExplorer.svelte';
</script>

<div class="container mx-auto p-4">
	<FileExplorer />
</div>
