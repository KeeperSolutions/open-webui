<script context="module" lang="ts">
	type RenderedDocument = {
		pdf?: ArrayBuffer;
		html?: string;
		slides?: string[];
		content?: string;
		workbook?: import('xlsx').WorkBook;
		sheet?: string;
	};

	// Documents already prepared this session, so switching back to one shows it at once
	const renderedDocuments = new Map<string, RenderedDocument>();
	const RENDERED_DOCUMENTS_LIMIT = 20;

	const cacheRendered = (key: string, rendered: RenderedDocument) => {
		renderedDocuments.set(key, rendered);
		if (renderedDocuments.size > RENDERED_DOCUMENTS_LIMIT) {
			renderedDocuments.delete(renderedDocuments.keys().next().value!);
		}
	};

	export const panelIconButtonClass =
		'p-1.5 rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-white transition disabled:opacity-60';

	// The pages' 1rem padding on each side plus room for the scrollbar
	const PAGES_SIDE_SPACE = 48;
</script>

<script lang="ts">
	import { getContext, onDestroy } from 'svelte';
	import { fade } from 'svelte/transition';
	import {
		config,
		selectedDocument,
		showArtifacts,
		showControls,
		showDocumentList,
		type ChatDocument
	} from '$lib/stores';
	import {
		documentFilename,
		documentKey,
		downloadDocument,
		driveActionLabel,
		fetchDocumentBuffer,
		openDocumentInDrive,
		savingDocumentKeys
	} from '$lib/utils/documents';
	import {
		PAGE_CONTENT_WIDTH,
		PAGE_HEIGHT,
		PAGE_MARGIN,
		PAGE_WIDTH,
		paginateDocument
	} from '$lib/utils/paginateDocument';

	import FilePreview from './FileNav/FilePreview.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Download from '../icons/Download.svelte';
	import XMark from '../icons/XMark.svelte';
	import GoogleDrive from '../icons/GoogleDrive.svelte';
	import ArrowsPointingOut from '../icons/ArrowsPointingOut.svelte';
	import ArrowsPointingIn from '../icons/ArrowsPointingIn.svelte';
	import ArrowLeft from '../icons/ArrowLeft.svelte';
	import Spinner from '../common/Spinner.svelte';

	const i18n = getContext('i18n');
	const t = (key: string) => $i18n.t(key);

	export let doc: ChatDocument;
	export let overlay = false;

	let expanded = false;
	let filePdfData: ArrayBuffer | null = null;
	let fileOfficeHtml: string | null = null;
	let fileOfficeSlides: string[] | null = null;
	let fileContent: string | null = null;
	let currentSlide = 0;
	let excelWorkbook: import('xlsx').WorkBook | null = null;
	let excelSheetNames: string[] = [];
	let selectedExcelSheet = '';
	// False only until the first document is ready, switching documents after that swaps them in place
	let shown = false;
	// True while a switched-to document is still loading behind the one on screen
	let pending = false;

	// Bumped on every load so a slow earlier load can't overwrite a newer document
	let loadId = 0;

	let probe: HTMLElement;
	let previewWidth = 0;
	// Pages keep their A4 size, so where they break never changes, and only scale down to fit a narrow panel
	$: pageZoom = previewWidth ? Math.min(1, (previewWidth - PAGES_SIDE_SPACE) / PAGE_WIDTH) : 1;
	$: saving = $savingDocumentKeys.includes(documentKey(doc));

	const sheetHtml = async (workbook: import('xlsx').WorkBook, sheet: string) => {
		const { excelToTable } = await import('$lib/utils/excelToTable');
		const result = await excelToTable(workbook.Sheets[sheet]);
		const DOMPurify = (await import('dompurify')).default;
		return DOMPurify.sanitize(result.html);
	};

	const renderSheet = async (sheet: string) => {
		if (!excelWorkbook) return;
		const id = loadId;
		selectedExcelSheet = sheet;
		const html = await sheetHtml(excelWorkbook, sheet);
		if (id === loadId) fileOfficeHtml = html;
	};

	const render = async (target: ChatDocument, id: number): Promise<RenderedDocument | null> => {
		const arrayBuffer = await fetchDocumentBuffer(target);
		if (id !== loadId) return null;

		if (target.format === 'pdf') {
			return { pdf: arrayBuffer };
		}
		if (target.format === 'docx') {
			const mammoth = await import('mammoth');
			// Without this mapping mammoth drops manual page breaks, which the pagination needs to see
			const res = await mammoth.convertToHtml(
				{ arrayBuffer },
				{ styleMap: ["br[type='page'] => hr.page-break"] }
			);
			const DOMPurify = (await import('dompurify')).default;
			await document.fonts?.ready;
			if (id !== loadId) return null;
			const pages = paginateDocument(DOMPurify.sanitize(res.value), probe);
			return {
				html: `<div class="document-pages">${pages
					.map((page) => `<div class="document-page">${page}</div>`)
					.join('')}</div>`
			};
		}
		if (target.format === 'xlsx') {
			const XLSX = await import('xlsx');
			const workbook = XLSX.read(new Uint8Array(arrayBuffer), { type: 'array' });
			const sheet = workbook.SheetNames[0] ?? '';
			return { workbook, sheet, html: sheet ? await sheetHtml(workbook, sheet) : '' };
		}
		if (target.format === 'pptx') {
			const { pptxToImages } = await import('$lib/utils/pptxToHtml');
			return { slides: (await pptxToImages(arrayBuffer)).images };
		}
		if (target.format === 'txt') {
			return { content: new TextDecoder().decode(arrayBuffer) };
		}
		return { content: $i18n.t('Preview is not available for this file type.') };
	};

	const show = (rendered: RenderedDocument) => {
		// pdf.js takes over the buffer it is given, so the cached one is copied each time
		filePdfData = rendered.pdf ? rendered.pdf.slice(0) : null;
		fileOfficeHtml = rendered.html ?? null;
		fileOfficeSlides = rendered.slides ?? null;
		fileContent = rendered.content ?? null;
		currentSlide = 0;
		excelWorkbook = rendered.workbook ?? null;
		excelSheetNames = rendered.workbook?.SheetNames ?? [];
		selectedExcelSheet = rendered.sheet ?? '';
		shown = true;
	};

	const load = async (target: ChatDocument) => {
		const id = ++loadId;
		const cached = renderedDocuments.get(documentKey(target));
		if (cached) {
			pending = false;
			show(cached);
			return;
		}

		pending = true;
		try {
			const rendered = await render(target, id);
			if (!rendered || id !== loadId) return;
			cacheRendered(documentKey(target), rendered);
			show(rendered);
		} catch (e) {
			console.error('Failed to preview document:', e);
			if (id === loadId) {
				show({
					content: `${t('Error previewing file')}: ${e instanceof Error ? e.message : t(`${e}`)}`
				});
			}
		} finally {
			if (id === loadId) pending = false;
		}
	};

	let loadedKey: string | null = null;
	$: if (doc && documentKey(doc) !== loadedKey) {
		loadedKey = documentKey(doc);
		load(doc);
	}

	// Full size has to leave the side pane, whose z-index would otherwise keep it under the sidebar and navbar
	const portal = (node: HTMLElement, enabled: boolean) => {
		const anchor = document.createComment('document-preview');
		node.before(anchor);
		const move = (toBody: boolean) => {
			if (toBody) document.body.appendChild(node);
			else anchor.after(node);
		};
		move(enabled);
		return {
			update: move,
			destroy: () => {
				anchor.remove();
				node.remove();
			}
		};
	};

	const close = () => {
		expanded = false;
		showControls.set(false);
		showArtifacts.set(false);
	};

	// Closing the panel by any route also deselects the document card
	onDestroy(() => {
		selectedDocument.set(null);
	});
</script>

<svelte:window
	on:keydown={(e) => {
		if (expanded && e.key === 'Escape') expanded = false;
	}}
/>

<div
	class="flex flex-col min-h-0 {expanded
		? 'fixed inset-0 z-[60] bg-white dark:bg-gray-850'
		: 'w-full h-full'}"
	use:portal={expanded}
>
	<div class="flex items-center justify-between gap-2 p-2.5 text-gray-900 dark:text-white shrink-0">
		<div class="flex items-center gap-2 min-w-0 pl-1">
			{#if $showDocumentList}
				<Tooltip content={$i18n.t('Back to files')}>
					<button
						class="{panelIconButtonClass} -ml-1"
						type="button"
						aria-label={$i18n.t('Back to files')}
						on:click={() => showArtifacts.set(false)}
					>
						<ArrowLeft className="size-4" />
					</button>
				</Tooltip>
			{/if}
			<div class="min-w-0 truncate text-sm">
				{doc.name}<span class="ml-1 text-gray-500 dark:text-gray-400"
					>· {(doc.format ?? '').toUpperCase()}</span
				>
			</div>
			{#if pending && shown}
				<Spinner className="size-3.5 shrink-0" />
			{/if}
		</div>

		<div class="flex items-center gap-0.5 shrink-0">
			{#if $config?.features?.enable_google_drive_connector}
				<Tooltip content={driveActionLabel(doc, t)}>
					<button
						class={panelIconButtonClass}
						type="button"
						aria-label={driveActionLabel(doc, t)}
						disabled={saving}
						on:click={() => openDocumentInDrive(doc, t)}
					>
						{#if saving}
							<Spinner className="size-4" />
						{:else}
							<GoogleDrive className="size-4 shrink-0" />
						{/if}
					</button>
				</Tooltip>
			{/if}

			<Tooltip content={$i18n.t('Download')}>
				<button
					class={panelIconButtonClass}
					type="button"
					aria-label={$i18n.t('Download')}
					on:click={() => downloadDocument(doc, t)}
				>
					<Download className="size-4" />
				</button>
			</Tooltip>

			<Tooltip content={expanded ? $i18n.t('Exit full screen') : $i18n.t('Open in full screen')}>
				<button
					class={panelIconButtonClass}
					type="button"
					aria-label={expanded ? $i18n.t('Exit full screen') : $i18n.t('Open in full screen')}
					on:click={() => (expanded = !expanded)}
				>
					{#if expanded}
						<ArrowsPointingIn className="size-4" />
					{:else}
						<ArrowsPointingOut className="size-4" />
					{/if}
				</button>
			</Tooltip>

			<Tooltip content={$i18n.t('Close')}>
				<button
					class={panelIconButtonClass}
					type="button"
					aria-label={$i18n.t('Close')}
					on:click={close}
				>
					<XMark className="size-4" />
				</button>
			</Tooltip>
		</div>
	</div>

	<div
		class="document-preview doc-{doc.format} flex-1 min-h-0 relative overflow-hidden bg-gray-100 dark:bg-gray-900"
		style="--page-width: {PAGE_WIDTH}px; --page-height: {PAGE_HEIGHT}px; --page-margin: {PAGE_MARGIN}px; --page-zoom: {pageZoom};"
		bind:clientWidth={previewWidth}
	>
		<div class="office-preview document-probe" aria-hidden="true">
			<div
				bind:this={probe}
				class="document-probe-content"
				style="width: {PAGE_CONTENT_WIDTH}px"
			></div>
		</div>

		{#if !shown}
			<div class="flex items-center justify-center h-full"><Spinner className="size-4" /></div>
		{:else}
			<div class="h-full" in:fade={{ duration: 200 }}>
				<FilePreview
					bind:currentSlide
					selectedFile={documentFilename(doc)}
					{filePdfData}
					{fileOfficeHtml}
					{fileOfficeSlides}
					{fileContent}
					{excelSheetNames}
					{selectedExcelSheet}
					onSheetChange={renderSheet}
					{overlay}
				/>
			</div>
		{/if}
	</div>
</div>

<style>
	/* Documents always read as white paper with dark text, like Word or Docs, in both themes */
	.document-preview :global(.office-preview) {
		background: transparent;
		color: #111827;
	}
	.document-preview :global(.office-preview:has(> .document-pages)) {
		padding: 1.5rem 1rem;
	}
	.document-preview :global(.document-pages) {
		zoom: var(--page-zoom);
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 1.5rem;
	}
	.document-preview :global(.document-page) {
		box-sizing: border-box;
		flex-shrink: 0;
		display: flow-root;
		width: var(--page-width);
		min-height: var(--page-height);
		padding: var(--page-margin);
		background: #fff;
		color: #111827;
		border-radius: 2px;
		box-shadow:
			0 1px 3px rgba(0, 0, 0, 0.12),
			0 4px 16px rgba(0, 0, 0, 0.06);
	}
	/* Same type as the generated docx, which follows Google Docs; the probe shares it so pages break where they're measured */
	.document-preview :global(:is(.document-page, .document-probe-content)) {
		font-family: Arial, sans-serif;
		font-size: 11pt;
		line-height: 1.15;
	}
	.document-preview :global(:is(.document-page, .document-probe-content) :is(h1, h2, h3, h4, h5, h6)) {
		font-weight: 400;
		line-height: 1.15;
	}
	.document-preview :global(:is(.document-page, .document-probe-content) h1) {
		font-size: 20pt;
		margin: 20pt 0 6pt;
		color: #000;
	}
	.document-preview :global(:is(.document-page, .document-probe-content) h2) {
		font-size: 16pt;
		margin: 18pt 0 6pt;
		color: #000;
	}
	.document-preview :global(:is(.document-page, .document-probe-content) h3) {
		font-size: 14pt;
		margin: 16pt 0 4pt;
		color: #434343;
	}
	.document-preview :global(:is(.document-page, .document-probe-content) :is(h4, h5, h6)) {
		font-size: 12pt;
		margin: 14pt 0 4pt;
		color: #666;
	}
	.document-preview :global(:is(.document-page, .document-probe-content) :is(h5, h6)) {
		font-size: 11pt;
	}
	.document-preview :global(:is(.document-page, .document-probe-content) p) {
		margin: 3pt 0 6pt;
	}
	/* Measures blocks at the real page width without being seen */
	.document-probe {
		position: absolute;
		top: 0;
		left: 0;
		height: 0;
		overflow: hidden;
		visibility: hidden;
		pointer-events: none;
	}
	/* Keeps margins inside the box the same way the padding of a real page does */
	.document-probe-content {
		display: flow-root;
	}
	/* FilePreview styles tables for spreadsheets, a Word table should wrap and fill the page instead */
	.document-preview.doc-docx :global(.office-preview table) {
		width: 100%;
		font-family: inherit;
		font-size: inherit;
		line-height: 1.4;
	}
	.document-preview.doc-docx :global(.office-preview table td),
	.document-preview.doc-docx :global(.office-preview table th) {
		white-space: normal;
		max-width: none;
		overflow: visible;
		cursor: auto;
		vertical-align: top;
	}
	.document-preview.doc-xlsx :global(.office-preview) {
		background: #fff;
	}
	.document-preview :global(.office-preview table td),
	.document-preview :global(.office-preview table th) {
		border-color: rgba(200, 200, 200, 0.5);
	}
	.document-preview :global(.office-preview table th.excel-col-hdr) {
		background: #f0f0f0;
		color: #666;
		border-bottom-color: rgba(180, 180, 180, 0.6);
	}
	.document-preview :global(.office-preview .excel-row-num) {
		background: #f0f0f0;
		color: #999;
		border-right-color: rgba(180, 180, 180, 0.6) !important;
	}
	.document-preview :global(.office-preview table tbody tr:nth-child(even) td:not(.excel-row-num)) {
		background: rgba(0, 0, 0, 0.015);
	}
	.document-preview :global(.office-preview table tbody tr:hover td:not(.excel-row-num)) {
		background: rgba(59, 130, 246, 0.06);
	}
</style>
