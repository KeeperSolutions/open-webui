import { get, writable } from 'svelte/store';
import { toast } from 'svelte-sonner';
import fileSaver from 'file-saver';
import {
	selectedDocument,
	showArtifacts,
	showControls,
	showEmbeds,
	showSettings,
	type ChatDocument
} from '$lib/stores';
import { downloadFileById } from '$lib/apis/files';
import { downloadGoogleDriveDocument, saveDocumentToGoogleDrive } from '$lib/apis/connectors';

const { saveAs } = fileSaver;

type Translate = (key: string) => string;

export const savingDocumentIds = writable<string[]>([]);

// A card is one file, known by its stored file_id, its Drive id, or both once it has been saved to Drive
export const documentKey = (doc: ChatDocument) => doc.file_id ?? doc.drive_id ?? '';

export const isSameDocument = (a: ChatDocument, b: ChatDocument) =>
	Boolean((a.file_id && a.file_id === b.file_id) || (a.drive_id && a.drive_id === b.drive_id));

export const documentFilename = (doc: ChatDocument) =>
	doc.format ? `${doc.name}.${doc.format}` : doc.name;

type HistoryWithDocuments = {
	messages?: Record<string, { timestamp?: number; documents?: ChatDocument[] }>;
};

const messagesInOrder = (history: HistoryWithDocuments) =>
	Object.values(history?.messages ?? {})
		.filter((message) => message.documents?.length)
		.sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));

// Every document in the chat once, in the order they first appeared, newer versions being separate files
export const collectChatDocuments = (history: HistoryWithDocuments) => {
	const collected: ChatDocument[] = [];
	for (const doc of messagesInOrder(history).flatMap((message) => message.documents ?? [])) {
		if (!collected.some((kept) => isSameDocument(kept, doc))) collected.push(doc);
	}
	return collected;
};

// Chats saved before cards were kept one per document can show the same file twice, so later copies fold into the first card
export const mergeDuplicateDocuments = (history: HistoryWithDocuments) => {
	const kept: ChatDocument[] = [];
	for (const message of messagesInOrder(history)) {
		message.documents = (message.documents ?? []).filter((doc) => {
			const first = kept.find((k) => isSameDocument(k, doc));
			if (!first) {
				kept.push(doc);
				return true;
			}
			Object.assign(first, doc, { format: first.format || doc.format });
			return false;
		});
	}
};

// The file itself, from our own storage or, for a card that only exists in Drive, through the Drive connector
const fetchDocumentBlob = async (doc: ChatDocument): Promise<Blob> => {
	if (doc.file_id) {
		const result = await downloadFileById(localStorage.token, doc.file_id);
		if (!result) throw 'Server connection failed';
		return result.blob;
	}
	const result = await downloadGoogleDriveDocument(
		localStorage.token,
		doc.drive_id ?? '',
		doc.format,
		documentFilename(doc)
	);
	if (!result) throw "Couldn't download this file from Google Drive.";
	return result.blob;
};

export const fetchDocumentBuffer = async (doc: ChatDocument) =>
	(await fetchDocumentBlob(doc)).arrayBuffer();

export const openDocumentPreview = (doc: ChatDocument) => {
	selectedDocument.set(doc);
	showEmbeds.set(false);
	showControls.set(true);
	showArtifacts.set(true);
};

export const downloadDocument = async (doc: ChatDocument) => {
	const blob = await fetchDocumentBlob(doc).catch((error) => {
		toast.error(`${error}`);
		return null;
	});
	if (blob) {
		saveAs(blob, documentFilename(doc));
	}
};

export const downloadAllDocuments = async (docs: ChatDocument[], t: Translate) => {
	const results = await Promise.all(docs.map((doc) => fetchDocumentBlob(doc).catch(() => null)));
	const failed = results.filter((result) => !result).length;
	if (failed === docs.length) {
		toast.error(t("Couldn't download the documents."));
		return;
	}

	const JSZip = (await import('jszip')).default;
	const zip = new JSZip();
	const used = new Set<string>();
	results.forEach((blob, i) => {
		if (!blob) return;
		const original = documentFilename(docs[i]);
		let filename = original;
		// Two documents with the same name would overwrite each other inside the zip
		for (let n = 2; used.has(filename); n++) {
			filename = original.replace(/(\.[^.]+)?$/, ` (${n})$1`);
		}
		used.add(filename);
		zip.file(filename, blob);
	});

	saveAs(await zip.generateAsync({ type: 'blob' }), 'documents.zip');
	if (failed) {
		toast.error(t("Some documents couldn't be downloaded and were left out of the zip."));
	}
};

// Saves the document to Drive on first use and mutates it in place with the returned link, so every holder of it sees the link
export const openDocumentInDrive = async (doc: ChatDocument, t: Translate) => {
	if (doc.web_link) {
		window.open(doc.web_link, '_blank', 'noopener,noreferrer');
		return;
	}
	// Without a link the card is one of ours not yet in Drive, so it has a stored file to save
	const fileId = doc.file_id;
	if (!fileId || get(savingDocumentIds).includes(fileId)) return;

	// Opened before the await so the popup blocker still sees it as part of the click
	const tab = window.open('', '_blank');
	savingDocumentIds.update((ids) => [...ids, fileId]);

	try {
		const saved = await saveDocumentToGoogleDrive(localStorage.token, fileId);
		Object.assign(doc, saved);

		if (tab) {
			tab.opener = null;
			tab.location.href = saved.web_link;
		} else {
			window.open(saved.web_link, '_blank', 'noopener,noreferrer');
		}
	} catch (error) {
		tab?.close();
		if (error === 'drive_not_connected') {
			toast.error(t('Connect Google Drive in Settings to save documents there.'), {
				action: {
					label: t('Settings'),
					onClick: () => showSettings.set({ tab: 'connectors' })
				}
			});
		} else {
			toast.error(`${error}`);
		}
	} finally {
		savingDocumentIds.update((ids) => ids.filter((id) => id !== fileId));
	}
};
