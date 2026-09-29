<script context="module" lang="ts">
	export const formatLabels: Record<string, string> = {
		pdf: 'PDF',
		docx: 'Word',
		xlsx: 'Excel',
		pptx: 'PowerPoint'
	};
</script>

<script lang="ts">
	import DriveXlsxGlyph from '$lib/components/icons/DriveXlsxGlyph.svelte';
	import DriveDocGlyph from '$lib/components/icons/DriveDocGlyph.svelte';

	export let format: string | null | undefined = null;

	// Matches Google Drive's own per-type colors (Docs blue, Sheets green, Slides yellow, PDF red)
	const formatColors: Record<string, string> = {
		pdf: '#E8918C',
		docx: '#7C93F5',
		xlsx: '#6FCF97',
		pptx: '#F5DFA0'
	};
</script>

<div
	class="flex items-center justify-center w-8 h-8 rounded-lg shrink-0"
	style="background-color: {formatColors[format ?? ''] ?? '#5f6368'}"
>
	{#if format === 'pdf'}
		<span class="text-[8px] font-bold text-white tracking-tight">PDF</span>
	{:else if format === 'xlsx'}
		<DriveXlsxGlyph />
	{:else}
		<!-- Also covers pptx: a blank slide, matching the plain colored-block look -->
		<DriveDocGlyph />
	{/if}
</div>
