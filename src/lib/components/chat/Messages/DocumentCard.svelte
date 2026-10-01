<script lang="ts">
	import { getContext } from 'svelte';
	import { config, selectedDocument, type ChatDocument } from '$lib/stores';
	import {
		documentKey,
		downloadAllDocuments,
		downloadDocument,
		driveActionLabel,
		openDocumentInDrive,
		openDocumentPreview,
		savingDocumentKeys
	} from '$lib/utils/documents';
	import DocumentFormatIcon, { documentTypeLabel } from './DocumentFormatIcon.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');
	const t = (key: string) => $i18n.t(key);

	export let documents: ChatDocument[] = [];

	let downloadingAll = false;

	$: hasDrive = !!$config?.features?.enable_google_drive_connector;
	$: selectedKey = $selectedDocument ? documentKey($selectedDocument) : null;
	$: isSaving = (doc: ChatDocument) => $savingDocumentKeys.includes(documentKey(doc));

	// A bit bigger on phones, where the buttons are tapped rather than clicked
	const buttonClass =
		'flex items-center gap-1.5 px-3.5 py-2 text-sm sm:px-3 sm:py-1.5 sm:text-xs font-medium rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-900 dark:text-white transition disabled:opacity-60';
	const iconClass = 'size-4 sm:size-3.5 shrink-0';
</script>

{#if documents.length > 0}
	<div class="mt-1 mb-2 w-full max-w-xl flex flex-col gap-1.5">
		{#each documents as doc (documentKey(doc))}
			<!-- The preview button's ::after covers the whole card, so a click anywhere opens it, while the actions stay separate buttons above it -->
			<div
				class="relative flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3 px-3 py-2.5 rounded-xl bg-white dark:bg-gray-850 border transition {selectedKey ===
				documentKey(doc)
					? 'border-gray-400 dark:border-gray-500'
					: 'border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700'}"
			>
				<button
					class="flex items-center gap-3 min-w-0 text-left cursor-pointer outline-none after:absolute after:inset-0 after:rounded-xl focus-visible:after:ring-2 focus-visible:after:ring-gray-400"
					type="button"
					aria-pressed={selectedKey === documentKey(doc)}
					on:click={() => openDocumentPreview(doc)}
				>
					<DocumentFormatIcon format={doc.format} />
					<div class="min-w-0">
						<!-- A long name wraps instead of being cut off -->
						<div class="text-sm text-gray-900 dark:text-white break-words">{doc.name}</div>
						<div class="text-xs text-gray-500 dark:text-gray-400">
							{documentTypeLabel(doc.format, t)}
						</div>
					</div>
				</button>

				<!-- On phones the buttons get a row of their own and share it equally, on wider screens they sit beside the name -->
				<div
					class="relative z-10 pointer-events-none [&>button]:pointer-events-auto flex items-center gap-1.5 shrink-0 max-sm:[&>button]:flex-1 max-sm:[&>button]:justify-center"
				>
					{#if hasDrive}
						<button
							class={buttonClass}
							type="button"
							disabled={isSaving(doc)}
							on:click={() => openDocumentInDrive(doc, t)}
						>
							{#if isSaving(doc)}
								<Spinner className={iconClass} />
							{:else}
								<GoogleDrive className={iconClass} />
							{/if}
							{driveActionLabel(doc, t)}
						</button>
					{/if}

					<button class={buttonClass} type="button" on:click={() => downloadDocument(doc, t)}>
						<Download className={iconClass} />
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
						await downloadAllDocuments(documents, t);
					} finally {
						downloadingAll = false;
					}
				}}
			>
				{#if downloadingAll}
					<Spinner className={iconClass} />
				{:else}
					<Download className={iconClass} />
				{/if}
				{$i18n.t('Download all')}
			</button>
		{/if}
	</div>
{/if}
