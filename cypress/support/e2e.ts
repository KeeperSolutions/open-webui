/// <reference types="cypress" />
// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="./index.d.ts" />

// ---------------------------------------------------------------------------
// Core-flow E2E support: shared fixtures + custom commands.
//
// This suite is a POST-UPGRADE REGRESSION GATE. The v0.9.6 -> v0.11.0 merge
// shipped several runtime-fatal bugs (config-key split-brain that killed the
// whole (app) layout onMount, tasks.py/tasks/ package shadowing, the
// "conversation stuck after an error" bug, an Ollama 400 on tool-incapable
// models) that ALL passed 639 backend unit tests + a clean svelte-check and
// only surfaced when actually driving the browser. See
// md-docs/upgrade-0.9.6-to-0.11.0.md.
//
// PREREQUISITES (see src/CLAUDE.md "E2E tests"):
//   - `npm run e2e` (scripts/e2e.sh): Vite dev server on :5173 + backend on
//     :8080 on a scratch DB. Cypress baseUrl is :5173.
//   - Ollama reachable with the CYPRESS_E2E_MODEL model pulled.
//   - The backend MUST point at a DISPOSABLE / SCRATCH DB. This suite
//     self-registers a real admin + a real user and cannot clean up.
//     NEVER run it against backend/data/webui.db with real data.
// ---------------------------------------------------------------------------

// A password that satisfies the optional password-validation regex
// (lower+upper+digit+symbol, >=8) when ENABLE_PASSWORD_VALIDATION=true, is
// fine when it's false (the default), and stays under bcrypt's 72-byte cap.
export const TEST_PASSWORD = 'Test1234!';

// The frontend (dev mode) calls the API at http://<hostname>:8080 —
// constants.ts:7 `WEBUI_HOSTNAME = ${location.hostname}:8080`. So
// `cy.request` for backend endpoints must target :8080 explicitly, NOT the
// Cypress baseUrl (:5173, the Vite dev server, which has no /api proxy).
// When Cypress points at a single-origin built instance (CYPRESS_BASE_URL),
// the API is same-origin and this falls back to baseUrl.
export const BACKEND_URL =
	Cypress.env('BACKEND_URL') ||
	(Cypress.config('baseUrl') || '').replace(/:5173$/, ':8080') ||
	'http://localhost:8080';

// Model under test — REQUIRED. Kept deterministic on purpose (every run
// exercises the same model + the search-filter code path).
const E2E_MODEL = Cypress.env('E2E_MODEL') as string | undefined;

before(() => {
	if (!E2E_MODEL) {
		throw new Error(
			'CYPRESS_E2E_MODEL is required. Set it to a model you have pulled locally, ' +
				'e.g. `CYPRESS_E2E_MODEL=gemma3:1b npm run e2e`.'
		);
	}

	// Run-scoped, synthetic-looking identities. No collision when the suite
	// is re-run against a DB that already has them, and obviously not real
	// accounts if they ever leak into a shared DB. Explicit
	// CYPRESS_E2E_*_EMAIL overrides still win.
	const stamp = Date.now();
	if (!Cypress.env('E2E_ADMIN_EMAIL')) {
		Cypress.env('E2E_ADMIN_EMAIL', `e2e-admin+${stamp}@test.local`);
	}
	if (!Cypress.env('E2E_USER_EMAIL')) {
		Cypress.env('E2E_USER_EMAIL', `e2e-user+${stamp}@test.local`);
	}
});

// Cypress clears localStorage between tests by default, so a one-time
// top-level `localStorage.setItem('locale', ...)` won't stick. Set it right
// before each `cy.visit` — text-based selectors (`Sign Out`, `User menu`,
// `Okay, Let's Go!`) depend on the en-US locale being active. (The spec
// runs testIsolation:false so state persists, but visits still re-init the
// app, so keep setting it per-visit.)
const forceEnLocale = () => {
	localStorage.setItem('locale', 'en-US');
};

// --- registerAdmin ---------------------------------------------------------
// First signup -> backend auto-promotes to admin (auths.py signup_handler)
// and disables further signup. HgAuthCard only starts in 'signup' mode when
// the page is reached via `?form=signup`.
Cypress.Commands.add('registerAdmin', () => {
	cy.visit('/auth?form=signup', { onBeforeLoad: forceEnLocale });

	cy.get('input#name').type('E2E Admin');
	cy.get('input#email').type(Cypress.env('E2E_ADMIN_EMAIL'));
	cy.get('input#password').type(TEST_PASSWORD);

	// Confirm-password field only renders when
	// enable_signup_password_confirmation is on — fill it only if present.
	cy.get('body').then(($body) => {
		if ($body.find('input#confirm-password').length) {
			cy.get('input#confirm-password').type(TEST_PASSWORD);
		}
	});

	cy.get('form button[type="submit"]').click();

	// setSessionUser -> goto('/chat')
	cy.url({ timeout: 20_000 }).should('include', '/chat');
	cy.dismissChangelog();
});

// --- dismissChangelog ----------------------------------------------------
// The "What's New" modal auto-shows for the fresh admin ($settings.version
// starts unset != $config.version) AND for anyone whose stored settings
// version lags $config.version. It mounts a beat AFTER /chat first paints
// (waits on the async $settings fetch), so a synchronous body check right
// after landing misses it — and then the modal (a portalled `.modal`
// overlay with a focus trap) covers the model button, the user menu and
// the chat input for every later step.
//
// Give the app a fixed moment to evaluate `showChangelog`, then: if the
// modal is up, close it via the aria-label="Close" (×) button and assert
// it's gone; if not, proceed. Only ever called right after a fresh
// login/signup, so the small fixed wait is acceptable.
Cypress.Commands.add('dismissChangelog', () => {
	// The composer being present is the "app is interactive" signal — wait
	// for it so we're past the reactive onMount work that toggles the modal.
	cy.get('#chat-input', { timeout: 20_000 }).should('exist');
	// eslint-disable-next-line cypress/no-unnecessary-waiting
	cy.wait(1500); // let showChangelog settle after $settings loads

	cy.get('body').then(($body) => {
		const modalUp =
			$body.find('[role="dialog"][aria-modal="true"]').length > 0 ||
			$body.text().includes("Okay, Let's Go!");
		if (!modalUp) {
			return;
		}
		if ($body.find('button[aria-label="Close"]').length) {
			cy.get('button[aria-label="Close"]').first().click({ force: true });
		} else {
			cy.contains('button', "Okay, Let's Go!").click({ force: true });
		}
		cy.get('[role="dialog"][aria-modal="true"]').should('not.exist');
		cy.contains("Okay, Let's Go!").should('not.exist');
	});
});

// --- selectModel ---------------------------------------------------------
// Model selector is a hand-rolled, virtualized, body-portalled dropdown.
// Filter via the search box (guarantees the target row is rendered) then
// click the row by its data-value (= exact model id).
Cypress.Commands.add('selectModel', (modelId: string) => {
	// Nothing should be covering the trigger by now (changelog dismissed),
	// but scroll it into view to be safe against sticky headers.
	cy.get('#model-selector-model-button').scrollIntoView();
	cy.get('#model-selector-model-button').click();
	cy.get('#model-search-input').should('be.visible');
	cy.get('#model-search-input').clear();
	cy.get('#model-search-input').type(modelId);
	cy.get(`[role="option"][data-value="${CSS.escape(modelId)}"]`).click();
	// Dropdown closes synchronously on select.
	cy.get('#model-search-input').should('not.exist');
});

// --- sendMessage -------------------------------------------------------
// #chat-input is a ProseMirror contenteditable (id is on the editable div,
// not a wrapper). Send button is disabled until there's text.
Cypress.Commands.add('sendMessage', (text: string) => {
	cy.get('#chat-input').scrollIntoView();
	cy.get('#chat-input').click();
	cy.get('#chat-input').type(text, { delay: 0 });
	cy.get('#send-message-button').should('not.be.disabled');
	cy.get('#send-message-button').click();
});

// --- createRegularUser -------------------------------------------------
// Admin-only. A plain 2nd signup is impossible (ENABLE_SIGNUP is flipped
// off + persisted on first admin) and would land as 'pending' anyway.
// POST /auths/add with role:'user' is what the admin UI's "Add User" does.
// NOTE: target BACKEND_URL, not baseUrl — the dev frontend and the API are
// on different ports.
Cypress.Commands.add('createRegularUser', (email: string) => {
	cy.window().then((win) => {
		const token = win.localStorage.getItem('token');
		expect(token, 'admin token in localStorage').to.be.a('string');

		cy.request({
			method: 'POST',
			url: `${BACKEND_URL}/api/v1/auths/add`,
			headers: { Authorization: `Bearer ${token}` },
			body: {
				name: 'E2E User',
				email,
				password: TEST_PASSWORD,
				role: 'user'
			}
		})
			.its('status')
			.should('eq', 200);
	});
});

// --- makeModelPublic -------------------------------------------------
// A freshly-pulled Ollama model is admin-only until an admin shares it
// (Admin -> Models -> access), so a `role: 'user'` account sees "No
// results found" in the model selector. This does what that UI does:
// POST /models/model/access/update with a wildcard read grant
// (principal_type:'user', principal_id:'*', permission:'read' == public).
// The endpoint auto-creates the DB model row for a bare Ollama id when
// the caller is an admin. Target BACKEND_URL (:8080), not baseUrl.
Cypress.Commands.add('makeModelPublic', (modelId: string) => {
	cy.window().then((win) => {
		const token = win.localStorage.getItem('token');
		expect(token, 'admin token in localStorage').to.be.a('string');

		cy.request({
			method: 'POST',
			url: `${BACKEND_URL}/api/v1/models/model/access/update`,
			headers: { Authorization: `Bearer ${token}` },
			body: {
				id: modelId,
				name: modelId,
				access_grants: [
					{ principal_type: 'user', principal_id: '*', permission: 'read' }
				]
			}
		})
			.its('status')
			.should('eq', 200);
	});
});

// --- logout ----------------------------------------------------------
Cypress.Commands.add('logout', () => {
	cy.get('button[aria-label="User menu"]').click();
	cy.contains('button', 'Sign Out').click();
	// UserMenu: location.href = res?.redirect_url ?? '/auth'
	cy.url({ timeout: 15_000 }).should('include', '/auth');
});

// --- loginViaForm -----------------------------------------------------
// Bare /auth => HgAuthCard signin mode. The spec runs with
// testIsolation:false (one continuous walkthrough), and this is only
// called once, so a plain form submit is enough — no cy.session needed.
Cypress.Commands.add('loginViaForm', (email: string, password: string) => {
	cy.visit('/auth', { onBeforeLoad: forceEnLocale });
	cy.get('input#email').type(email);
	cy.get('input#password').type(password);
	cy.get('form button[type="submit"]').click();
	cy.url({ timeout: 20_000 }).should('include', '/chat');
	cy.dismissChangelog();
});
