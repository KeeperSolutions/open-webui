<script lang="ts">
	import HgBadge from './HgBadge.svelte';

	export let variant: 'success' | 'info' | 'warning' | 'neutral' = 'neutral';
	export let label: string;
</script>

<HgBadge {variant}>{label}</HgBadge>
