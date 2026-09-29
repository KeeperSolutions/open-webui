<script lang="ts">
	import { getContext } from 'svelte';
	import { config, selectedDocument, type ChatDocument } from '$lib/stores';
	import {
		documentKey,
		downloadAllDocuments,
		downloadDocument,
		openDocumentInDrive,
		openDocumentPreview,
		savingDocumentIds
	} from '$lib/utils/documents';
	import DocumentFormatIcon, { documentTypeLabel } from './DocumentFormatIcon.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	export let documents: ChatDocument[] = [];

	let downloadingAll = false;

	const buttonClass =
		'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-900 dark:text-white transition disabled:opacity-60';
</script>

{#if documents.length > 0}
	<div class="mt-1 mb-2 w-full max-w-xl flex flex-col gap-1.5">
		{#each documents as doc (documentKey(doc))}
			<div
				class="flex items-center justify-between gap-3 px-3 py-2.5 rounded-xl bg-white dark:bg-gray-850 border cursor-pointer transition {$selectedDocument &&
				documentKey($selectedDocument) === documentKey(doc)
					? 'border-gray-400 dark:border-gray-500'
					: 'border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700'}"
				role="button"
				tabindex="0"
				aria-pressed={!!$selectedDocument && documentKey($selectedDocument) === documentKey(doc)}
				on:click={() => openDocumentPreview(doc)}
				on:keydown={(e) => {
					if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
						e.preventDefault();
						openDocumentPreview(doc);
					}
				}}
			>
				<div class="flex items-center gap-3 min-w-0">
					<DocumentFormatIcon format={doc.format} />
					<div class="min-w-0">
						<div class="text-sm text-gray-900 dark:text-white truncate">{doc.name}</div>
						<div class="text-xs text-gray-500 dark:text-gray-400">
							{documentTypeLabel(doc.format, (key) => $i18n.t(key))}
						</div>
					</div>
				</div>

				<div class="flex items-center gap-1.5 shrink-0">
					{#if $config?.features?.enable_google_drive_connector}
						<button
							class={buttonClass}
							type="button"
							disabled={$savingDocumentIds.includes(documentKey(doc))}
							on:click|stopPropagation={async () => {
								await openDocumentInDrive(doc, (key) => $i18n.t(key));
								documents = documents;
							}}
						>
							{#if $savingDocumentIds.includes(documentKey(doc))}
								<Spinner className="size-3.5" />
							{:else}
								<GoogleDrive className="size-3.5 shrink-0" />
							{/if}
							{doc.web_link ? $i18n.t('Open in Drive') : $i18n.t('Add to Drive')}
						</button>
					{/if}

					<button
						class={buttonClass}
						type="button"
						on:click|stopPropagation={() => downloadDocument(doc)}
					>
						<Download className="size-3.5" />
						{$i18n.t('Download')}
					</button>
				</div>
			</div>
		{/each}

		{#if documents.length > 1}
			<button
				class="{buttonClass} w-fit mt-0.5"
				type="button"
				disabled={downloadingAll}
				on:click={async () => {
					downloadingAll = true;
					try {
						await downloadAllDocuments(documents, (key) => $i18n.t(key));
					} finally {
						downloadingAll = false;
					}
				}}
			>
				{#if downloadingAll}
					<Spinner className="size-3.5" />
				{:else}
					<Download className="size-3.5" />
				{/if}
				{$i18n.t('Download all')}
			</button>
		{/if}
	</div>
{/if}
