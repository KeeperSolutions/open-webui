<script context="module" lang="ts">
	import type { ChatDocument } from '$lib/stores';
	import type { Translate } from '$lib/utils/documents';

	const formatKinds: Record<string, string> = {
		docx: 'Document',
		xlsx: 'Spreadsheet',
		pptx: 'Presentation',
		txt: 'Text'
	};

	// Reads like "Document · DOCX", or just "PDF" where the kind and the extension are the same word
	export const documentTypeLabel = (format: ChatDocument['format'], t: Translate) => {
		const ext = (format ?? '').toUpperCase();
		const kind = formatKinds[format ?? ''];
		return kind ? `${t(kind)} · ${ext}` : ext || t('File');
	};
</script>

<script lang="ts">
	import DriveXlsxGlyph from '$lib/components/icons/DriveXlsxGlyph.svelte';
	import DriveDocGlyph from '$lib/components/icons/DriveDocGlyph.svelte';
	import DriveSlidesGlyph from '$lib/components/icons/DriveSlidesGlyph.svelte';

	export let format: ChatDocument['format'] = null;

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
	{:else if format === 'pptx'}
		<DriveSlidesGlyph />
	{:else}
		<!-- Also covers unknown formats, matching the plain colored-block look -->
		<DriveDocGlyph />
	{/if}
</div>
