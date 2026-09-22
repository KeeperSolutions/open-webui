/// <reference types="cypress" />

import { TEST_PASSWORD } from '../support/e2e';

// ---------------------------------------------------------------------------
// SPEC 01 of 2 — RUN ORDER MATTERS (files are numbered; Cypress runs specs
// alphabetically). This spec does the one-and-only signup and, crucially,
// CREATES + APPROVES the regular user and SHARES the model with them.
// Spec 02 (02-chat-depth.cy.ts) then logs in AS that regular user — it
// depends on this spec having run first against the same backend.
//
// Core flows — one sequence, top to bottom:
//   first signup -> admin -> admin runs their flow -> admin creates the
//   regular user + shares the model -> regular user runs their flow.
//
// Each `it()` depends on the state left by the previous one (Cypress runs
// them in order within a file). This is deliberate — it mirrors how a
// person actually exercises the app in one sitting, and keeps the suite to
// a single signup + a single real LLM round-trip per role.
//
// See cypress/support/e2e.ts for prerequisites (scratch DB, Ollama +
// CYPRESS_E2E_MODEL) and the custom commands used here.
// ---------------------------------------------------------------------------

describe('Core flows (post-upgrade smoke)', () => {
	// ===================== Admin (first signup) =====================

	it('first signup becomes an authenticated admin on /chat', () => {
		cy.registerAdmin();
		cy.url().should('include', '/chat');
		// The chat composer being present is the practical "app is usable"
		// signal — the config-key-splitbrain bug this session fixed left the
		// whole (app) layout onMount half-dead, and nothing below it worked.
		cy.get('#chat-input').should('exist');
	});

	it('admin can select the configured model', () => {
		const model = Cypress.env('E2E_MODEL') as string;
		cy.selectModel(model);
		// Trigger button reflects the picked model. Match loosely on the
		// short name (drop the `:tag`) since the label is the display name.
		const shortName = model.split(':')[0];
		cy.get('#model-selector-model-button').should(
			'contain.text',
			shortName
		);
	});

	it('admin sees the Playground (admin-only) sidebar entry', () => {
		// Positive control for the permission-boundary check the regular
		// user does later.
		cy.get('button[aria-label="User menu"]').click();
		cy.contains('Playground').should('exist');
		cy.get('body').type('{esc}');
	});

	it('admin sends a message and gets a completed response', () => {
		const prompt = 'Reply with exactly the single word: pong';
		cy.sendMessage(prompt);

		// User message is echoed into the transcript.
		cy.contains(prompt).should('exist');

		// Generation finished AND succeeded — real output, no error UI.
		// (A bare [aria-label="Edit"] check passes green on a failed
		// generation too: chat:message:error also sets message.done.)
		cy.assertAssistantResponded();
	});

	it('admin creates an approved regular user', () => {
		// Admin is still authenticated here (token in localStorage), which
		// createRegularUser requires.
		cy.createRegularUser(Cypress.env('E2E_USER_EMAIL'));
	});

	it('admin shares the model so non-admins can use it', () => {
		// A freshly-pulled Ollama model is admin-only by default. Without
		// this the regular user's model selector shows "No results found".
		// This mirrors Admin -> Models -> (model) -> access = public.
		cy.makeModelPublic(Cypress.env('E2E_MODEL') as string);
	});

	it('admin logs out and lands on /auth', () => {
		cy.logout();
		cy.url().should('include', '/auth');
	});

	// ===================== Regular (non-admin) user =====================

	it('regular user logs in via the sign-in form', () => {
		cy.loginViaForm(Cypress.env('E2E_USER_EMAIL'), TEST_PASSWORD);
		cy.url().should('include', '/chat');
		cy.get('#chat-input').should('exist');
	});

	it('regular user does NOT see the Playground admin entry', () => {
		cy.get('button[aria-label="User menu"]').click();
		cy.contains('Playground').should('not.exist');
		cy.get('body').type('{esc}');
	});

	it('regular user can select a model and gets a completed response', () => {
		const model = Cypress.env('E2E_MODEL') as string;
		cy.selectModel(model);

		const prompt = 'Reply with exactly the single word: pong';
		cy.sendMessage(prompt);
		cy.contains(prompt).should('exist');
		cy.assertAssistantResponded();
	});

	it('regular user logs out and lands on /auth', () => {
		cy.logout();
		cy.url().should('include', '/auth');
	});
});
