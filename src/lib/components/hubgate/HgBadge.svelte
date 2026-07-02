<script lang="ts">
	export let variant: 'success' | 'info' | 'warning' | 'neutral' = 'neutral';

	const variantClasses: Record<typeof variant, string> = {
		success: 'bg-hg-success-50 text-hg-success-700',
		info: 'bg-hg-info-bg text-hg-info-text',
		warning: 'bg-hg-warning-100 text-hg-warning-600',
		neutral: 'bg-hg-bg-muted text-hg-text-secondary'
	};
</script>

<span
	class="inline-flex items-center h-7 px-3 rounded-hg-full font-hg-body font-medium text-xs whitespace-nowrap {variantClasses[variant]}"
>
	<slot />
</span>
