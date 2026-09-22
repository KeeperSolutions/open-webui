import { defineConfig } from 'cypress';

export default defineConfig({
	e2e: {
		// `scripts/e2e.sh` (npm run e2e) runs the Vite DEV server on :5173
		// (frontend always in sync with src/ — no stale-build footgun) with
		// the backend on :8080. Override with CYPRESS_BASE_URL to point at a
		// built instance on :8080 (npm run cy:run) or a deployed URL.
		baseUrl: process.env.CYPRESS_BASE_URL || 'http://localhost:5173',
		specPattern: 'cypress/e2e/**/*.cy.ts',
		supportFile: 'cypress/support/e2e.ts',
		// The core-flow spec is ONE sequence: first signup -> admin flow ->
		// admin creates the regular user -> regular-user flow. Each `it()`
		// builds on the state (auth token, current page) left by the previous
		// one. Cypress 13 defaults `testIsolation: true`, which wipes
		// cookies/localStorage and parks the browser on about:blank between
		// every test — that breaks this sequence (test 2 onward would start
		// logged out on a blank page). Turn it off so the file runs as the
		// single walkthrough it's written as.
		testIsolation: false,
		// Real LLM generation is slow; the per-spec timeout is bumped
		// locally where it matters (waiting for a response to complete).
		defaultCommandTimeout: 10_000,
		video: false,
		// Desktop width so only the desktop sidebar's user menu renders
		// (a second, collapsed <UserMenu> exists for mobile — avoid an
		// ambiguous match on `[aria-label="User menu"]`).
		viewportWidth: 1280,
		viewportHeight: 800
	}
});
