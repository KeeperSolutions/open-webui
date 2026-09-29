<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import fileSaver from 'file-saver';
	import { config, selectedDocumentId, showSettings } from '$lib/stores';
	import { downloadFileById } from '$lib/apis/files';
	import { saveDocumentToGoogleDrive } from '$lib/apis/connectors';
	import DocumentFormatIcon, { formatLabels } from './DocumentFormatIcon.svelte';

	const { saveAs } = fileSaver;
	const i18n = getContext('i18n');

	type Document = {
		file_id: string;
		name: string;
		format: string;
		drive_id?: string;
		web_link?: string | null;
	};

	export let documents: Document[] = [];

	let savingIds: string[] = [];

	const download = async (doc: Document) => {
		const result = await downloadFileById(localStorage.token, doc.file_id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (result) {
			saveAs(result.blob, result.filename ?? `${doc.name}.${doc.format}`);
		}
	};

	const openInDrive = async (doc: Document) => {
		if (doc.web_link) {
			window.open(doc.web_link, '_blank', 'noopener,noreferrer');
			return;
		}
		if (savingIds.includes(doc.file_id)) return;

		// Opened before the await so the popup blocker still sees it as part of the click
		const tab = window.open('', '_blank');
		savingIds = [...savingIds, doc.file_id];

		try {
			const saved = await saveDocumentToGoogleDrive(localStorage.token, doc.file_id);
			// Mutated in place so the message's own documents list keeps the link too
			Object.assign(doc, saved);
			documents = documents;

			if (tab) {
				tab.opener = null;
				tab.location.href = saved.web_link;
			} else {
				window.open(saved.web_link, '_blank', 'noopener,noreferrer');
			}
		} catch (error) {
			tab?.close();
			if (error === 'drive_not_connected') {
				toast.error($i18n.t('Connect Google Drive in Settings to save documents there.'), {
					action: {
						label: $i18n.t('Settings'),
						onClick: () => showSettings.set({ tab: 'connectors' })
					}
				});
			} else {
				toast.error(`${error}`);
			}
		} finally {
			savingIds = savingIds.filter((id) => id !== doc.file_id);
		}
	};

	const select = (doc: Document) => {
		selectedDocumentId.set(doc.file_id);
	};
</script>

{#if documents.length > 0}
	<div class="mt-1 mb-2 w-full flex flex-col gap-1.5">
		{#each documents as doc (doc.file_id)}
			<div
				class="flex items-center justify-between gap-2.5 px-3 py-2 rounded-xl bg-gray-900 dark:bg-black border cursor-pointer transition {$selectedDocumentId ===
				doc.file_id
					? 'border-gray-400'
					: 'border-gray-800 hover:border-gray-700'}"
				role="button"
				tabindex="0"
				aria-pressed={$selectedDocumentId === doc.file_id}
				on:click={() => select(doc)}
				on:keydown={(e) => {
					if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
						e.preventDefault();
						select(doc);
					}
				}}
			>
				<div class="flex items-center gap-2.5 min-w-0">
					<DocumentFormatIcon format={doc.format} />
					<div class="min-w-0">
						<div class="text-sm text-white truncate">{doc.name}</div>
						<div class="text-[0.6875rem] text-gray-400">
							{formatLabels[doc.format ?? ''] ?? $i18n.t('File')}
						</div>
					</div>
				</div>

				<div class="flex items-center gap-2 shrink-0">
					{#if $config?.features?.enable_google_drive_connector}
						<button
							class="px-3 py-1.5 text-xs font-medium border border-gray-700 text-white hover:bg-gray-800 transition rounded-full disabled:opacity-60"
							type="button"
							disabled={savingIds.includes(doc.file_id)}
							on:click|stopPropagation={() => openInDrive(doc)}
						>
							{savingIds.includes(doc.file_id) ? $i18n.t('Saving...') : $i18n.t('Open in Drive')}
						</button>
					{/if}

					<button
						class="px-3 py-1.5 text-xs font-medium bg-white hover:bg-gray-100 text-black transition rounded-full"
						type="button"
						on:click|stopPropagation={() => download(doc)}
					>
						{doc.format ? `${$i18n.t('Download')} .${doc.format}` : $i18n.t('Download')}
					</button>
				</div>
			</div>
		{/each}
	</div>
{/if}
