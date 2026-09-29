<script lang="ts">
	import { getContext } from 'svelte';
	import fileSaver from 'file-saver';
	import { downloadGoogleDriveDocument } from '$lib/apis/connectors';
	import DocumentFormatIcon, { formatLabels } from './DocumentFormatIcon.svelte';

	const { saveAs } = fileSaver;
	const i18n = getContext('i18n');

	type DriveDocument = {
		id: string;
		name: string;
		format?: string;
		web_link?: string;
	};

	export let driveDocuments: DriveDocument[] = [];

	const download = async (doc: DriveDocument) => {
		const token = localStorage.token;
		const result = await downloadGoogleDriveDocument(token, doc.id, doc.format, doc.name);
		if (result) {
			saveAs(result.blob, result.filename ?? doc.name);
		}
	};
</script>

{#if driveDocuments.length > 0}
	<div class="mt-1 mb-2 w-full flex flex-col gap-1.5">
		{#each driveDocuments as doc (doc.id)}
			<div
				class="flex items-center justify-between gap-2.5 px-3 py-2 rounded-xl bg-gray-900 dark:bg-black border border-gray-800"
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
					{#if doc.web_link}
						<a
							href={doc.web_link}
							target="_blank"
							rel="noopener noreferrer"
							class="px-3 py-1.5 text-xs font-medium border border-gray-700 text-white hover:bg-gray-800 transition rounded-full"
						>
							{$i18n.t('Open in Drive')}
						</a>
					{/if}

					<button
						class="px-3 py-1.5 text-xs font-medium bg-white hover:bg-gray-100 text-black transition rounded-full"
						type="button"
						on:click={() => download(doc)}
					>
						{doc.format ? `${$i18n.t('Download')} .${doc.format}` : $i18n.t('Download')}
					</button>
				</div>
			</div>
		{/each}
	</div>
{/if}
