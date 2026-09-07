import { describe, it, expect } from 'vitest';
import { isEventForLoadedChat } from './chatEvents';

const messages = { 'msg-abc': { id: 'msg-abc', role: 'assistant' } };

describe('isEventForLoadedChat', () => {
	it('accepts an event for the chat currently open', () => {
		expect(isEventForLoadedChat({ chat_id: 'c1', message_id: 'msg-abc' }, 'c1', messages)).toBe(
			true
		);
	});

	it('rejects an event for a different chat whose message we do not have', () => {
		expect(isEventForLoadedChat({ chat_id: 'other', message_id: 'msg-zzz' }, 'c1', messages)).toBe(
			false
		);
	});

	it('accepts an event that arrives before a new chat id is known', () => {
		// THE regression. A brand-new chat has no id client-side: the backend
		// generates it on the first request and Chat.svelte only learns it when
		// that request's HTTP response returns. Chat processing runs as a
		// background task and can emit well before then — a turn refused fast
		// (an oversized prompt rejected by the PII masking budget guard) emits
		// `chat:message:error` and `chat:tasks:cancel` within milliseconds.
		// Matching on chat_id alone discards both, so the error never renders
		// and the spinner never stops; the error reaches the user only after a
		// page reload, from the database.
		expect(
			isEventForLoadedChat({ chat_id: 'brand-new', message_id: 'msg-abc' }, '', messages)
		).toBe(true);
	});

	it('still rejects an unknown message once a chat is open', () => {
		expect(
			isEventForLoadedChat({ chat_id: 'brand-new', message_id: 'msg-zzz' }, 'c1', messages)
		).toBe(false);
	});

	it('is defensive about a missing event, message id or history', () => {
		expect(isEventForLoadedChat(null, 'c1', messages)).toBe(false);
		expect(isEventForLoadedChat({ chat_id: 'c1' }, 'c1', null)).toBe(true);
		expect(isEventForLoadedChat({ message_id: 'msg-abc' }, 'c1', undefined)).toBe(false);
	});

	it('does not treat an inherited object property as a known message', () => {
		// `history.messages` is a plain object; a message id like "constructor"
		// must not match Object.prototype.
		expect(
			isEventForLoadedChat({ chat_id: 'brand-new', message_id: 'constructor' }, 'c1', messages)
		).toBe(false);
	});
});
