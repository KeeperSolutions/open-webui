import { get, writable } from 'svelte/store';
import { toast } from 'svelte-sonner';
import fileSaver from 'file-saver';
import {
	chatId,
	selectedDocument,
	showArtifacts,
	showControls,
	showDocumentList,
	showEmbeds,
	showSettings,
	type ChatDocument
} from '$lib/stores';
import { downloadFileById } from '$lib/apis/files';
import { downloadGoogleDriveDocument, saveDocumentToGoogleDrive } from '$lib/apis/connectors';

const { saveAs } = fileSaver;

export type Translate = (key: string) => string;

// Keys of the documents being saved to Drive right now, so every card and preview of one shows it
export const savingDocumentKeys = writable<string[]>([]);

// A change to a card made outside a chat event (a Drive save from a button), applied by Chat.svelte like one
export const documentUpdates = writable<ChatDocument | null>(null);

// A card is one file, known by its stored file_id, its Drive id, or both once it has been saved to Drive
export const documentKey = (doc: ChatDocument) => doc.file_id ?? doc.drive_id ?? '';

export const isSameDocument = (a: ChatDocument, b: ChatDocument) =>
	Boolean((a.file_id && a.file_id === b.file_id) || (a.drive_id && a.drive_id === b.drive_id));

export const documentFilename = (doc: ChatDocument) =>
	doc.format ? `${doc.name}.${doc.format}` : doc.name;

export const driveActionLabel = (doc: ChatDocument, t: Translate) =>
	t(doc.web_link ? 'Open in Drive' : 'Add to Drive');

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

type LegacyDriveDocument = { id: string; name: string; format: string; web_link?: string };

// Chats saved before document cards kept Drive files in message.driveDocuments, which nothing renders anymore
export const migrateDriveDocuments = (history: {
	messages?: Record<string, { documents?: ChatDocument[]; driveDocuments?: LegacyDriveDocument[] }>;
}) => {
	for (const message of Object.values(history?.messages ?? {})) {
		if (!message.driveDocuments) continue;
		const documents = message.documents ?? [];
		for (const old of message.driveDocuments) {
			const extension = `.${old.format}`;
			const doc = {
				drive_id: old.id,
				name: old.name.toLowerCase().endsWith(extension)
					? old.name.slice(0, -extension.length)
					: old.name,
				format: old.format,
				web_link: old.web_link
			};
			if (!documents.some((kept) => isSameDocument(kept, doc))) documents.push(doc);
		}
		message.documents = documents;
		delete message.driveDocuments;
	}
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

// Merges a change into the card the chat already shows for that document, and says whether there was one
export const updateDocumentCard = (history: HistoryWithDocuments, data: ChatDocument) => {
	const owner = Object.values(history?.messages ?? {}).find((message) =>
		message.documents?.some((doc) => isSameDocument(doc, data))
	);
	if (!owner?.documents) return false;

	owner.documents = owner.documents.map((doc) =>
		isSameDocument(doc, data)
			? Object.assign(doc, data, { format: doc.format || data.format })
			: doc
	);
	// The open preview holds the same object, so it is told to pick up the change too
	const selected = get(selectedDocument);
	if (selected && isSameDocument(selected, data)) selectedDocument.set(selected);
	return true;
};

// The file itself, from our own storage or, for a card that only exists in Drive, through the Drive connector
const fetchDocumentBlob = async (doc: ChatDocument): Promise<Blob> => {
	if (doc.file_id) return downloadFileById(localStorage.token, doc.file_id);

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

export const openDocumentList = () => {
	showArtifacts.set(false);
	showEmbeds.set(false);
	showDocumentList.set(true);
	showControls.set(true);
};

// Artifacts share the side panel with the document preview, which would otherwise stay on top of them
export const openArtifacts = () => {
	selectedDocument.set(null);
	showEmbeds.set(false);
	showControls.set(true);
	showArtifacts.set(true);
};

export const downloadDocument = async (doc: ChatDocument, t: Translate) => {
	const blob = await fetchDocumentBlob(doc).catch((error) => {
		toast.error(t(`${error}`));
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

// Opens the document in Drive, saving it there first if it isn't yet, and reports the new link to every card of it
export const openDocumentInDrive = async (doc: ChatDocument, t: Translate) => {
	if (doc.web_link) {
		window.open(doc.web_link, '_blank', 'noopener,noreferrer');
		return;
	}
	// Without a link the card is one of ours not yet in Drive, so it has a stored file to save
	const fileId = doc.file_id;
	if (!fileId || get(savingDocumentKeys).includes(fileId)) return;

	// Opened before the await so the popup blocker still sees it as part of the click
	const tab = window.open('', '_blank');
	savingDocumentKeys.update((keys) => [...keys, fileId]);

	try {
		const saved = await saveDocumentToGoogleDrive(localStorage.token, fileId, get(chatId));
		documentUpdates.set(saved);

		if (tab) {
			tab.opener = null;
			tab.location.href = saved.web_link;
		} else {
			// The blank tab was blocked, and a new one opened after the await would be too, so the link is offered instead
			toast.success(t('Saved to Google Drive.'), {
				action: {
					label: t('Open in Drive'),
					onClick: () => window.open(saved.web_link, '_blank', 'noopener,noreferrer')
				}
			});
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
		} else if (error === 'drive_write_not_granted') {
			toast.error(
				t('Reconnect Google Drive in Settings and allow access to save documents there.'),
				{
					action: {
						label: t('Settings'),
						onClick: () => showSettings.set({ tab: 'connectors' })
					}
				}
			);
		} else {
			toast.error(t(`${error}`));
		}
	} finally {
		savingDocumentKeys.update((keys) => keys.filter((key) => key !== fileId));
	}
};
