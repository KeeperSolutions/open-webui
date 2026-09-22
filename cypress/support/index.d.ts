/// <reference types="cypress" />

declare namespace Cypress {
	interface Chainable {
		/**
		 * First-user signup (auto-promoted to admin by the backend). Visits
		 * `/auth?form=signup`, fills the HgAuthCard form, submits, waits for
		 * `/chat`, then dismisses the "What's New" changelog modal.
		 * Only works against an EMPTY / disposable DB.
		 */
		registerAdmin(): Chainable<void>;

		/**
		 * Dismiss the changelog ("Okay, Let's Go!") modal if it's showing.
		 * No-op if it isn't. Safe to call anywhere.
		 */
		dismissChangelog(): Chainable<void>;

		/**
		 * Open the chat model selector, filter to `modelId` via the search
		 * box, and click its row. `modelId` is the exact Ollama model id/tag
		 * (e.g. `gemma3:1b`).
		 */
		selectModel(modelId: string): Chainable<void>;

		/**
		 * Type `text` into the chat input and click Send (asserting the
		 * button is enabled first).
		 */
		sendMessage(text: string): Chainable<void>;

		/**
		 * Wait for the last assistant turn to finish SUCCESSFULLY: the
		 * done-gated action row appears, the "Regenerate this response…"
		 * error hint does NOT, and `#response-content-container` has
		 * non-empty text. Catches backend/LLM errors that a bare
		 * `[aria-label="Edit"]` check would pass green (the error path also
		 * sets `message.done`). `timeout` defaults to 120s (real LLM gen).
		 */
		assertAssistantResponded(timeout?: number): Chainable<void>;

		/**
		 * Create an APPROVED regular (`role: 'user'`) account via the
		 * admin-only `POST /api/v1/auths/add`. Requires an authenticated
		 * admin session (reads `localStorage.token`).
		 */
		createRegularUser(email: string): Chainable<void>;

		/**
		 * Make an Ollama/OpenAI model visible to non-admin users by giving it
		 * a public read grant (`user:*:read`), via the admin-only
		 * `POST /api/v1/models/model/access/update`. Freshly-pulled models are
		 * admin-only by default, so the regular-user chat flow needs this.
		 * Requires an authenticated admin session.
		 */
		makeModelPublic(modelId: string): Chainable<void>;

		/** Open the sidebar user menu and click Sign Out. */
		logout(): Chainable<void>;

		/**
		 * Log in via the sign-in form. Used for the regular user after the
		 * admin has created their account.
		 */
		loginViaForm(email: string, password: string): Chainable<void>;

		/**
		 * Log in as the E2E_USER_EMAIL regular (non-admin) account. That
		 * account + its model access are set up by spec 01
		 * (01-core-flow.cy.ts) — this asserts it exists (with a run-order
		 * hint if not) before doing the form login.
		 */
		loginAsRegularUser(): Chainable<void>;
	}
}
