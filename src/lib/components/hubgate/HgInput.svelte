<script lang="ts">
	export let label = '';
	export let placeholder = '';
	export let value = '';
	export let type: 'text' | 'email' | 'password' = 'text';
	export let name = '';
	export let id = '';
	export let required = false;
	export let disabled = false;
	export let caption = '';
	export let error = false;
	export let autocomplete: HTMLInputElement['autocomplete'] = '';
	export let showLeadingIcon = true;

	import HgIconMail from '$lib/components/icons/HgIconMail.svelte';
	import HgIconKey from '$lib/components/icons/HgIconKey.svelte';
	import HgIconUser from '$lib/components/icons/HgIconUser.svelte';
	import HgIconEyeOpen from '$lib/components/icons/HgIconEyeOpen.svelte';
	import HgIconEyeClosed from '$lib/components/icons/HgIconEyeClosed.svelte';

	// Allow hiding the reveal toggle for password fields if ever needed.
	export let showRevealToggle = true;

	let revealed = false;
	let inputEl: HTMLInputElement;

	$: isPassword = type === 'password';

	// Svelte forbids a dynamic `type` attribute alongside `bind:value`, so set it
	// imperatively: password fields become 'text' when revealed.
	$: if (inputEl) {
		inputEl.type = isPassword && revealed ? 'text' : type;
	}

	$: inputId = id || name;

	$: borderClass = error
		? 'border-2 border-hg-error-text bg-hg-error-bg'
		: disabled
			? 'border border-hg-border-subtle bg-hg-bg-muted'
			: 'border border-hg-border bg-hg-bg-surface focus-within:border-2 focus-within:border-hg-border-focus';
</script>

<div class="flex flex-col gap-1 w-full">
	{#if label}
		<label
			for={inputId}
			class="font-hg-body text-xs font-normal leading-[1.4] {error
				? 'text-hg-error-text'
				: disabled
					? 'text-hg-text-tertiary'
					: 'text-hg-text-secondary'}"
		>
			{label}
		</label>
	{/if}

	<div
		class="flex items-center gap-2 h-11 px-2 rounded-hg-md transition-colors {borderClass} {disabled
			? 'opacity-70 cursor-not-allowed'
			: ''}"
	>
		{#if showLeadingIcon}
			<span
				class="shrink-0 {error
					? 'text-hg-error-text'
					: disabled
						? 'text-hg-text-tertiary'
						: value
							? 'text-hg-text-primary'
							: 'text-hg-text-tertiary'}"
				aria-hidden="true"
			>
				{#if type === 'password'}
					<HgIconKey class="text-hg-text-tertiary" />
				{:else if type === 'email'}
					<HgIconMail class="text-hg-text-tertiary" />
				{:else}
					<HgIconUser class="text-hg-text-tertiary" />
				{/if}
			</span>
		{/if}

		<input
			bind:this={inputEl}
			{type}
			{name}
			id={inputId}
			{required}
			{disabled}
			{placeholder}
			{autocomplete}
			bind:value
			on:input
			on:change
			on:focus
			on:blur
			class="flex-1 min-w-0 bg-transparent font-hg-body text-sm font-normal leading-[1.4]
				text-hg-text-primary placeholder:text-hg-text-tertiary
				outline-none disabled:cursor-not-allowed"
		/>

		{#if isPassword && showRevealToggle}
			<button
				type="button"
				class="shrink-0 text-hg-text-tertiary hover:text-hg-text-primary transition-colors disabled:cursor-not-allowed"
				aria-label={revealed ? 'Hide password' : 'Show password'}
				aria-pressed={revealed}
				{disabled}
				on:click={() => (revealed = !revealed)}
			>
				{#if revealed}
					<HgIconEyeOpen />
				{:else}
					<HgIconEyeClosed />
				{/if}
			</button>
		{/if}
	</div>

	{#if caption}
		<p class="font-hg-body text-xs leading-[1.4] {error ? 'text-hg-error-text' : 'text-hg-text-tertiary'}">
			{caption}
		</p>
	{/if}
</div>
