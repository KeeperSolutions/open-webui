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
		// A new chat has no id on the client yet: the backend creates it on the
		// first request, and Chat.svelte receives it only when that request's
		// HTTP response returns. Events emitted before then, such as
		// `chat:message:error` for a prompt refused by the PII masking size
		// check, must still be accepted. Otherwise the error is not shown and
		// the spinner keeps running.
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
		// `history.messages` is a plain object; a message id such as
		// "constructor" must not match a property of Object.prototype.
		expect(
			isEventForLoadedChat({ chat_id: 'brand-new', message_id: 'constructor' }, 'c1', messages)
		).toBe(false);
	});
});
