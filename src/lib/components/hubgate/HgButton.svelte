<script lang="ts">
	export let variant: 'primary' | 'secondary' | 'ghost' | 'inverse' | 'destructive' | 'link' | 'sso' = 'primary';
	export let size: 'sm' | 'md' | 'lg' = 'md';
	export let type: 'button' | 'submit' | 'reset' = 'button';
	export let disabled = false;
	export let loading = false;
	export let fullWidth = false;

	const sizeClasses: Record<typeof size, string> = {
		sm: 'h-8 px-4 text-sm gap-1.5',
		md: 'h-10 px-5 text-sm gap-2',
		lg: 'h-12 px-6 text-base gap-2'
	};

	const variantClasses: Record<typeof variant, string> = {
		primary:
			'bg-hg-blue hover:bg-hg-blue-hover text-white rounded-hg-full border-transparent',
		secondary:
			'bg-white hover:bg-hg-bg-muted text-hg-text-primary border border-hg-border rounded-hg-md',
		ghost:
			'bg-transparent hover:bg-hg-bg-muted text-hg-text-primary border-transparent rounded-hg-md',
		inverse:
			'bg-hg-text-primary hover:bg-hg-text-secondary text-white rounded-hg-full border-transparent',
		destructive:
			'bg-hg-error-text hover:bg-red-800 text-white rounded-hg-full border-transparent',
		link: 'bg-transparent text-hg-blue hover:underline border-transparent rounded-hg-md px-0',
		sso: 'bg-white hover:bg-hg-bg-muted text-hg-text-primary border border-hg-border rounded-hg-full'
	};
</script>

<button
	{type}
	{disabled}
	class="
		inline-flex items-center justify-center font-hg-body font-medium
		transition-colors duration-150 select-none whitespace-nowrap
		focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hg-border-focus focus-visible:ring-offset-1
		disabled:opacity-50 disabled:cursor-not-allowed
		{sizeClasses[size]}
		{variantClasses[variant]}
		{fullWidth ? 'w-full' : ''}
	"
	on:click
	on:keydown
	aria-busy={loading}
>
	{#if loading}
		<svg
			class="animate-spin {size === 'sm' ? 'size-3.5' : 'size-4'} shrink-0"
			xmlns="http://www.w3.org/2000/svg"
			fill="none"
			viewBox="0 0 24 24"
			aria-hidden="true"
		>
			<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
			<path
				class="opacity-75"
				fill="currentColor"
				d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
			/>
		</svg>
	{/if}
	<slot />
</button>
