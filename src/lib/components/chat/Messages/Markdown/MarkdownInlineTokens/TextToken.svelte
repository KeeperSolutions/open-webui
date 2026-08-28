<script lang="ts">
	export let token;
	export let done = true;
<<<<<<< HEAD
=======

	$: raw = token?.raw ?? '';
>>>>>>> v0.11.0
</script>

{#if done}
	{raw}
{:else}
<<<<<<< HEAD
	{#each (token?.raw ?? '').split(' ') as text}
=======
	{#each raw.split(' ') as text}
>>>>>>> v0.11.0
		<span class="fade-in-token">
			{text}{' '}
		</span>
	{/each}
{/if}
