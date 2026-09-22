/// <reference types="cypress" />

// ---------------------------------------------------------------------------
// SPEC 02 of 2 — RUN ORDER MATTERS. This runs AFTER 01-core-flow.cy.ts
// (files are numbered; Cypress runs specs alphabetically), which creates
// the regular user and shares the model with them. This spec logs in as
// that REGULAR (non-admin) user and exercises chat depth.
//
// Why regular-user, not admin: persistence, reload, sidebar history and
// regenerate are things every user does — not admin features. A merge can
// break the persist/reload path or a chat permission (chat.regenerate_
// response is a real per-role permission) for non-admins only, while the
// admin path and every backend unit test stay green.
//
// One user, one sequence, past "a message round-trips" into the areas the
// v0.9.6 -> v0.11.0 merge actually churned:
//   - PERSISTENCE: the `chat` blob is saved, survives a full reload, and
//     re-decrypts (EncryptedJSONField / ENC1: — TRAU-434).
//   - SIDEBAR history + reopen: chat shows in the sidebar; "New Chat"
//     clears the transcript and the URL (/ vs /c/<id> — the routing
//     question from the merge); clicking the row reloads the messages.
//   - REGENERATE: the {#if message.done}-gated action row works; a
//     regenerated response branches (sibling versions -> the prev/next
//     pager appears). Same `message.done` / process_chat_response area as
//     this session's "stuck after error" fix.
//
// If run in isolation (`--spec 02-...`) against a backend where spec 01
// never ran, `loginAsRegularUser` fails loudly with a run-order hint.
//
// Prereqs: same as spec 01 — `npm run e2e` (scratch DB + Ollama +
// CYPRESS_E2E_MODEL). See cypress/README.md.
// ---------------------------------------------------------------------------

const MODEL = () => Cypress.env('E2E_MODEL') as string;
const PROMPT = 'Reply with exactly the single word: pong';
const GEN_TIMEOUT = 120_000;

describe('Chat depth — regular user (post-upgrade)', () => {
	it('regular user is authenticated on /chat', () => {
		cy.loginAsRegularUser();
		cy.url().should('include', '/chat');
		cy.get('#chat-input').should('exist');
	});

	it('a chat is created, streamed, and persisted', () => {
		// The model was shared with this user by spec 01.
		cy.selectModel(MODEL());
		cy.sendMessage(PROMPT);

		cy.contains(PROMPT).should('exist');
		// Generation finished AND succeeded (real output, no error UI) —
		// not just message.done, which the error path also sets.
		cy.assertAssistantResponded(GEN_TIMEOUT);

		// A conversation id is now in the URL — the chat was saved.
		cy.url().should('match', /\/c\/[0-9a-f-]{36}/i);
	});

	it('the chat survives a full page reload (save + re-decrypt)', () => {
		cy.url().then((chatUrl) => {
			cy.reload();
			// Same conversation, and BOTH turns rehydrated from the
			// persisted (and, on staging/prod, encrypted) `chat` blob —
			// not a fresh page.
			cy.url().should('eq', chatUrl);
			cy.contains(PROMPT, { timeout: 20_000 }).should('exist'); // user turn
			cy.get('[aria-label="Edit"]').should('exist'); // assistant turn
			// The assistant's actual text came back non-empty — the round
			// trip through save + EncryptedJSONField decrypt worked, not
			// just an empty message shell.
			cy.get('#response-content-container')
				.last()
				.invoke('text')
				.then((t) =>
					expect(
						t.trim(),
						'reloaded assistant text'
					).to.have.length.greaterThan(0)
				);
		});
	});

	it('the chat is listed in the sidebar history', () => {
		// The `#sidebar-new-chat-button` id is on TWO elements:
		//   - a hidden `<button class="hidden">` hotkey trigger (always in
		//     the DOM, even when the sidebar is collapsed)
		//   - the expanded-sidebar entry: an `<a href="/">` (this one)
		// primeAppState seeds localStorage.sidebar='true' so the sidebar is
		// expanded and the anchor renders; target it specifically.
		cy.get('a#sidebar-new-chat-button').should('be.visible');
		// The history row links to /c/<id>. At least one exists now.
		cy.get('a[href^="/c/"]').should('have.length.at.least', 1);
	});

	it('"New Chat" clears the transcript and the URL', () => {
		cy.get('a#sidebar-new-chat-button').click();
		// New Chat goes to `/` (Sidebar.newChatHandler -> goto('/')), NOT a
		// /c/<id> conversation URL.
		cy.url().should('not.match', /\/c\//);
		cy.contains(PROMPT).should('not.exist'); // transcript cleared
		cy.get('#chat-input').should('exist');
	});

	it('reopening the chat from the sidebar reloads its messages', () => {
		cy.get('a[href^="/c/"]').first().click();
		cy.url().should('match', /\/c\/[0-9a-f-]{36}/i);
		cy.contains(PROMPT, { timeout: 20_000 }).should('exist');
		cy.get('[aria-label="Edit"]').should('be.visible');
	});

	it('regenerating the response produces a new, completed version', () => {
		// `$settings.regenerateMenu` defaults on, so the Regenerate button
		// opens a "Suggest a change" dropdown (Try Again / Add Details /
		// More Concise) rather than regenerating directly. "Try Again" is
		// the plain regenerate (RegenerateMenu.svelte -> onRegenerate()).
		cy.get('[aria-label="Regenerate"]:visible').first().click();
		cy.contains('button', 'Try Again').click();

		// Branching: the response now has sibling versions, so the
		// prev/next-message pager renders (it only appears when
		// siblings.length > 1).
		cy.get('[aria-label="Previous message"]', {
			timeout: GEN_TIMEOUT
		}).should('be.visible');
		// And the regenerated turn completed successfully (real output,
		// no error UI).
		cy.assertAssistantResponded(GEN_TIMEOUT);
	});

	it('the regenerated chat still survives a reload', () => {
		cy.url().then((chatUrl) => {
			cy.reload();
			cy.url().should('eq', chatUrl);
			cy.contains(PROMPT, { timeout: 20_000 }).should('exist');
			// The version pager persisted too — both responses were saved.
			cy.get('[aria-label="Previous message"]').should('be.visible');
		});
	});
});
