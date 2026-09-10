<script lang="ts">
	import { getContext } from 'svelte';
	import fileSaver from 'file-saver';
	import { downloadGoogleDriveDocument } from '$lib/apis/connectors';

	const { saveAs } = fileSaver;
	const i18n = getContext('i18n');

	export let driveDocuments = [];

	const formatLabels: Record<string, string> = {
		pdf: 'PDF',
		docx: 'Word',
		xlsx: 'Excel',
		pptx: 'PowerPoint'
	};

	const download = async (doc: { id: string; name: string; format: string }) => {
		const token = localStorage.token;
		const blob = await downloadGoogleDriveDocument(token, doc.id, doc.format, doc.name);
		if (blob) {
			saveAs(blob, `${doc.name}.${doc.format}`);
		}
	};
</script>

{#if driveDocuments.length > 0}
	<div class="mt-1 mb-2 w-full flex flex-col gap-1.5">
		{#each driveDocuments as doc (doc.id)}
			<div
				class="flex items-center justify-between gap-2.5 px-3 py-2 rounded-xl border border-gray-100 dark:border-gray-800"
			>
				<div class="min-w-0">
					<div class="text-sm text-gray-900 dark:text-white truncate">{doc.name}</div>
					<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{formatLabels[doc.format] ?? doc.format.toUpperCase()}
					</div>
				</div>

				<div class="flex items-center gap-2 shrink-0">
					{#if doc.web_link}
						<a
							href={doc.web_link}
							target="_blank"
							rel="noopener noreferrer"
							class="px-3 py-1.5 text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 transition rounded-full"
						>
							{$i18n.t('Open in Drive')}
						</a>
					{/if}

					<button
						class="px-3 py-1.5 text-xs font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
						type="button"
						on:click={() => download(doc)}
					>
						{$i18n.t('Download')} .{doc.format}
					</button>
				</div>
			</div>
		{/each}
	</div>
{/if}
