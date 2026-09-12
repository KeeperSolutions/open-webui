/**
 * Returns whether a websocket chat event belongs to the chat history loaded in
 * the chat view.
 *
 * Comparing only `event.chat_id === $chatId` drops events for a new chat. The
 * backend creates the chat id on the first request, and the client receives it
 * only when that request's HTTP response returns (`chatId.set(res.chat_id)` in
 * Chat.svelte). Chat processing runs as a background task and can emit events
 * before then. For example, a prompt refused by the PII masking size check
 * emits `chat:message:error` and `chat:tasks:cancel` within milliseconds of the
 * request. If those events are dropped, the error is not shown and the spinner
 * keeps running; the user sees the error only after reloading the page.
 *
 * The function therefore checks the message id first. The client generates it
 * as a uuid before sending the request, so an event whose message id is in the
 * loaded history belongs to this chat whatever `chatId` currently holds. All
 * other events, including events for messages this client did not create, are
 * matched by chat id.
 */
export function isEventForLoadedChat(
	event: { chat_id?: string | null; message_id?: string | null } | null | undefined,
	chatId: string | null | undefined,
	messages: Record<string, unknown> | null | undefined
): boolean {
	if (!event) return false;

	const messageId = event.message_id;
	// Use `hasOwnProperty`, not `in` or a truthiness check. `history.messages` is
	// a plain object, so a message id such as "constructor" or "toString" would
	// otherwise match a property of Object.prototype and accept an event from
	// another chat.
	if (messageId && messages && Object.prototype.hasOwnProperty.call(messages, messageId)) {
		return true;
	}

	return event.chat_id === chatId;
}
