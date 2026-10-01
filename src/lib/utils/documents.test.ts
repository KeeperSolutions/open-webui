// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import {
	collectChatDocuments,
	isSameDocument,
	mergeDuplicateDocuments,
	migrateDriveDocuments
} from './documents';

const report = { file_id: 'f1', name: 'Report', format: 'docx' };

describe('isSameDocument', () => {
	it('matches a stored document with its Drive copy and a Drive-only card by its Drive id', () => {
		expect(isSameDocument(report, { ...report, drive_id: 'd1' })).toBe(true);
		expect(
			isSameDocument({ ...report, drive_id: 'd1' }, { drive_id: 'd1', name: 'x', format: 'docx' })
		).toBe(true);
		expect(isSameDocument(report, { file_id: 'f2', name: 'Report', format: 'docx' })).toBe(false);
		expect(isSameDocument({ name: 'a', format: 'pdf' }, { name: 'a', format: 'pdf' })).toBe(false);
	});
});

describe('collectChatDocuments', () => {
	it('lists each document once, in the order the messages came in', () => {
		const history = {
			messages: {
				b: {
					timestamp: 2,
					documents: [
						{ ...report, drive_id: 'd1' },
						{ drive_id: 'd2', name: 'Copy', format: 'xlsx' }
					]
				},
				a: { timestamp: 1, documents: [report] },
				c: { timestamp: 3 }
			}
		};

		expect(collectChatDocuments(history).map((doc) => doc.name)).toEqual(['Report', 'Copy']);
	});
});

describe('mergeDuplicateDocuments', () => {
	it('folds a later copy of a document into its first card', () => {
		const first = { ...report };
		const history = {
			messages: {
				a: { timestamp: 1, documents: [first] },
				b: { timestamp: 2, documents: [{ ...report, drive_id: 'd1', web_link: 'link' }] }
			}
		};

		mergeDuplicateDocuments(history);

		expect(history.messages.a.documents).toEqual([
			{ file_id: 'f1', name: 'Report', format: 'docx', drive_id: 'd1', web_link: 'link' }
		]);
		expect(history.messages.b.documents).toEqual([]);
		expect(history.messages.a.documents[0]).toBe(first);
	});
});

describe('migrateDriveDocuments', () => {
	it('turns the old driveDocuments of a message into document cards', () => {
		const history = {
			messages: {
				a: {
					timestamp: 1,
					documents: [{ drive_id: 'd1', name: 'Plan', format: 'docx', web_link: 'new' }],
					driveDocuments: [
						{ id: 'd1', name: 'Plan', format: 'docx', web_link: 'old' },
						{ id: 'd2', name: 'Budget.pdf', format: 'pdf', web_link: 'link' }
					]
				},
				b: { timestamp: 2 }
			}
		};

		migrateDriveDocuments(history);

		expect(history.messages.a.documents).toEqual([
			{ drive_id: 'd1', name: 'Plan', format: 'docx', web_link: 'new' },
			{ drive_id: 'd2', name: 'Budget', format: 'pdf', web_link: 'link' }
		]);
		expect(history.messages.a).not.toHaveProperty('driveDocuments');
		expect(history.messages.b).toEqual({ timestamp: 2 });
	});
});
