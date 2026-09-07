/**
 * Whether a websocket chat event belongs to the history currently loaded in
 * the chat view.
 *
 * The obvious test — `event.chat_id === $chatId` — silently drops events for a
 * BRAND-NEW chat. The backend generates the chat id on the first request and
 * the client only learns it when that request's HTTP response returns
 * (`chatId.set(res.chat_id)` in Chat.svelte), while chat processing runs as a
 * background task that can emit long before then. A turn that fails fast — an
 * oversized prompt refused by the PII masking budget guard, say — emits
 * `chat:message:error` and `chat:tasks:cancel` within milliseconds of the POST.
 * Matched on chat id alone, both are discarded: the error never renders, the
 * spinner never stops, and the error reaches the user only on a page reload,
 * read back from the database it was correctly written to.
 *
 * The message id is the sounder key. It is a uuid generated on the client
 * before the request goes out, so an event whose message is present in the
 * loaded history is unambiguously ours whatever `chatId` currently holds. The
 * chat-id comparison is kept as-is for everything else, including events that
 * arrive for messages this client never registered.
 */
export function isEventForLoadedChat(
	event: { chat_id?: string | null; message_id?: string | null } | null | undefined,
	chatId: string | null | undefined,
	messages: Record<string, unknown> | null | undefined
): boolean {
	if (!event) return false;

	const messageId = event.message_id;
	// `hasOwnProperty`, not `in` or a truthiness check: `history.messages` is a
	// plain object, so a message id of "constructor" or "toString" would
	// otherwise match Object.prototype and admit an event for another chat.
	if (messageId && messages && Object.prototype.hasOwnProperty.call(messages, messageId)) {
		return true;
	}

	return event.chat_id === chatId;
}
