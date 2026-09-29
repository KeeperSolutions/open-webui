<script context="module" lang="ts">
	export type RenderedDocument = {
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

	const cacheRendered = (fileId: string, rendered: RenderedDocument) => {
		renderedDocuments.set(fileId, rendered);
		if (renderedDocuments.size > RENDERED_DOCUMENTS_LIMIT) {
			renderedDocuments.delete(renderedDocuments.keys().next().value!);
		}
	};
</script>

<script lang="ts">
	import { getContext, onDestroy, tick } from 'svelte';
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
		documentKey,
		downloadDocument,
		fetchDocumentBuffer,
		openDocumentInDrive,
		savingDocumentIds
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
	$: pageZoom = previewWidth ? Math.min(1, (previewWidth - 48) / PAGE_WIDTH) : 1;

	const sheetHtml = async (workbook: import('xlsx').WorkBook, sheet: string) => {
		const { excelToTable } = await import('$lib/utils/excelToTable');
		const result = await excelToTable(workbook.Sheets[sheet]);
		const DOMPurify = (await import('dompurify')).default;
		return DOMPurify.sanitize(result.html);
	};

	const renderSheet = async (sheet: string) => {
		if (!excelWorkbook) return;
		selectedExcelSheet = sheet;
		fileOfficeHtml = await sheetHtml(excelWorkbook, sheet);
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
			if (!probe) await tick();
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
					content: `${$i18n.t('Error previewing file')}: ${e instanceof Error ? e.message : e}`
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

	const iconButtonClass =
		'p-1.5 rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-white transition disabled:opacity-60';

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
						class="{iconButtonClass} -ml-1"
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
				<Tooltip content={doc.web_link ? $i18n.t('Open in Drive') : $i18n.t('Add to Drive')}>
					<button
						class={iconButtonClass}
						type="button"
						aria-label={doc.web_link ? $i18n.t('Open in Drive') : $i18n.t('Add to Drive')}
						disabled={$savingDocumentIds.includes(documentKey(doc))}
						on:click={async () => {
							await openDocumentInDrive(doc, (key) => $i18n.t(key));
							doc = doc;
						}}
					>
						{#if $savingDocumentIds.includes(documentKey(doc))}
							<Spinner className="size-4" />
						{:else}
							<GoogleDrive className="size-4 shrink-0" />
						{/if}
					</button>
				</Tooltip>
			{/if}

			<Tooltip content={$i18n.t('Download')}>
				<button
					class={iconButtonClass}
					type="button"
					aria-label={$i18n.t('Download')}
					on:click={() => downloadDocument(doc)}
				>
					<Download className="size-4" />
				</button>
			</Tooltip>

			<Tooltip content={expanded ? $i18n.t('Exit full screen') : $i18n.t('Open in full screen')}>
				<button
					class={iconButtonClass}
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
					class={iconButtonClass}
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

		{#if overlay}
			<div class="absolute top-0 left-0 right-0 bottom-0 z-10"></div>
		{/if}
		{#if !shown}
			<div class="flex items-center justify-center h-full"><Spinner className="size-4" /></div>
		{:else}
			<div class="h-full" in:fade={{ duration: 200 }}>
				<FilePreview
					bind:currentSlide
					selectedFile={`${doc.name}.${doc.format}`}
					{filePdfData}
					{fileOfficeHtml}
					{fileOfficeSlides}
					{fileContent}
					{excelSheetNames}
					{selectedExcelSheet}
					onSheetChange={renderSheet}
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
	/* Measures blocks at the real page width without being seen, and flow-root matches how margins sit inside a padded page */
	.document-probe {
		position: absolute;
		top: 0;
		left: 0;
		height: 0;
		overflow: hidden;
		visibility: hidden;
		pointer-events: none;
	}
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
