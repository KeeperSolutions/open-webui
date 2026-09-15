/// <reference types="cypress" />

// ---------------------------------------------------------------------------
// SPEC 03 of 3 — RUN ORDER MATTERS (files are numbered; Cypress runs specs
// alphabetically). Reuses the admin account 01-core-flow.cy.ts creates
// (signup / first-admin promotion) and the model it pulls + shares
// (Cypress.env('E2E_MODEL')) — this spec logs the admin back in rather than
// repeating that setup.
//
// Covers the TRAU-542 "Featured Models" admin curation + model-selector
// surfacing end to end:
//   admin opens Admin -> Settings -> Models -> "Featured models"
//   -> curates the E2E model with a provider name + tags
//   -> save is blocked by field-limit violations (provider required,
//      provider length, tag length) before it's accepted — mirrors
//      featuredModels.ts / the backend _validate_featured_models validator,
//      but exercised through the real form this time
//   -> the chat model selector opens on the "Featured" pill and renders the
//      curated provider / name / tags
//
// All selectors are scoped inside the modal dialog ([role="dialog"]) — the
// admin Models page behind it has its own <select>/<input> elements that
// would otherwise collide.
//
// See cypress/support/e2e.ts for prerequisites (scratch DB, Ollama +
// CYPRESS_E2E_MODEL) and the custom commands used here.
// ---------------------------------------------------------------------------

describe('Featured Models (admin curation + model selector)', () => {
	const model = Cypress.env('E2E_MODEL') as string;
	const providerName = 'E2E Provider';
	// Modal.svelte portals to document.body, and the Featured Models modal opens
	// ON TOP of the still-open Settings modal — so [role="dialog"] matches TWO
	// elements while it's up. Always target the last (topmost) one.
	const modalDialog = () => cy.get('[role="dialog"][aria-modal="true"]').last();

	// "Settings" in the admin top bar sets $showSettings = 'admin:general',
	// mounting SettingsModal.svelte (chat/SettingsModal.svelte) — a searchable
	// sidebar of tabs, NOT the flat admin/Settings.svelte tab bar. It renders
	// the same admin/Settings/Models.svelte this feature lives in, just reached
	// through a different shell. The tab buttons carry no stable id, so filter
	// the sidebar via its search box down to one match instead.
	const openAdminModelsSettings = () => {
		cy.get('button[aria-label="User menu"]').click();
		cy.contains('Admin Panel').click();
		cy.url({ timeout: 15_000 }).should('include', '/admin');
		cy.contains('a', 'Settings').click();

		// Modal.svelte's content is `{#if show}`-gated — every open is a fresh
		// mount, entering with a 200ms flyAndScale + fade transition
		// (utils/transitions/index.ts). `.should('not.be.disabled')` alone
		// isn't enough: the input is never actually `disabled`, but mid-
		// transition it's intermittently not yet actionable, which Cypress
		// (seen ~1 run in 10-ish under `e2e:flake`) reports through the same
		// "targeted a disabled element" wording as a real disabled attribute.
		// Give the transition time to finish before the first interaction.
		cy.get('#search-input-settings-modal', { timeout: 15_000 }).should('be.visible');
		// eslint-disable-next-line cypress/no-unnecessary-waiting
		cy.wait(300);
		cy.get('#search-input-settings-modal').clear();
		cy.get('#search-input-settings-modal').type('models');
		cy.get('button[role="tab"]').contains('Models').click();
	};

	it('admin logs in', () => {
		// Signup is disabled after 01-core-flow's first admin, so this
		// form-logs-in — same fallback path registerAdmin() takes internally.
		cy.registerAdmin();
	});

	it('admin opens Settings -> Models (via the settings modal)', () => {
		openAdminModelsSettings();

		// admin/Settings/Models.svelte reads from the $models store — wait for
		// the toolbar (and therefore the store) to be ready before opening the
		// Featured modal.
		cy.contains('button', 'Featured models').should('be.visible');
	});

	it('opens the Featured Models modal', () => {
		cy.contains('button', 'Featured models').click();
		modalDialog()
			.contains('Featured models are shown at the top of the model selector')
			.should('be.visible');
	});

	it('adding a model with no provider name blocks Save', () => {
		modalDialog().within(() => {
			cy.get('select').first().select(model, { force: true });
			// A fresh entry starts with an empty provider_name — Save must be
			// disabled immediately, before any typing.
			cy.contains('button', 'Save').should('be.disabled');
		});
	});

	it('a too-short provider name blocks Save with the length hint', () => {
		modalDialog().within(() => {
			cy.get('input[id^="featured-model-provider-"]').first().clear();
			cy.get('input[id^="featured-model-provider-"]').first().type('ab');
			cy.contains('button', 'Save').should('be.disabled');
			cy.contains('3').should('be.visible'); // inline "3–24 characters" hint
		});
	});

	it('a valid provider name unblocks Save', () => {
		modalDialog().within(() => {
			cy.get('input[id^="featured-model-provider-"]').first().clear();
			cy.get('input[id^="featured-model-provider-"]')
				.first()
				.type(providerName);
			cy.contains('button', 'Save').should('not.be.disabled');
		});
	});

	it('a tag over the 10-char limit blocks Save via the toast error', () => {
		modalDialog().within(() => {
			// The input has maxlength=10, so typing can't exceed it — set the
			// value directly (bypasses the HTML constraint, the way a paste or
			// programmatic set would) to exercise the Svelte-side validator
			// (validateFeaturedModels), not just the HTML attribute.
			cy.get('input[id^="featured-model-tag-"]')
				.first()
				.invoke('val', 'way-too-long-tag')
				.trigger('input');
			cy.contains('button', 'Save').should('be.disabled');
		});
		modalDialog().contains('button', 'Save').click({ force: true });
		cy.contains('Each tag must be 10 characters or fewer.').should(
			'be.visible'
		);
	});

	it('fills in curated tags within the limit and saves successfully', () => {
		modalDialog().within(() => {
			cy.get('input[id^="featured-model-tag-"]').first().clear();
			cy.get('input[id^="featured-model-tag-"]').first().type('fast');

			cy.get('input[id^="featured-model-tag-"]').eq(1).clear();
			cy.get('input[id^="featured-model-tag-"]').eq(1).type('smart');
			cy.get('input[id^="featured-model-tag-"]').eq(2).clear();
			cy.get('input[id^="featured-model-tag-"]').eq(2).type('e2e');

			cy.contains('button', 'Save').should('not.be.disabled').click();
		});
		cy.contains('Featured models saved successfully').should('be.visible');
		// Modal closes on a successful save.
		cy.contains(
			'Featured models are shown at the top of the model selector'
		).should('not.exist');
	});

	it('closes the admin panel and returns to chat', () => {
		cy.get('body').type('{esc}');
		cy.visit('/chat');
		cy.get('#chat-input', { timeout: 20_000 }).should('exist');
		cy.dismissChangelog();
	});

	it('the model selector opens on the Featured pill', () => {
		cy.get('#model-selector-model-button').scrollIntoView();
		cy.get('#model-selector-model-button').click();
		cy.get('#model-search-input').should('be.visible');

		cy.contains('button', 'Featured').should(
			'have.attr',
			'aria-pressed',
			'true'
		);
		cy.get('[role="listbox"][aria-label="Featured models"]').should(
			'be.visible'
		);
	});

	it('the featured card shows the curated provider, name, and tags', () => {
		cy.get('[role="listbox"][aria-label="Featured models"]').within(() => {
			cy.contains(providerName).should('be.visible');
			cy.contains('fast').should('be.visible');
			cy.contains('smart').should('be.visible');
			cy.contains('e2e').should('be.visible');
		});

		// Close the dropdown without changing the selection.
		cy.get('body').type('{esc}');
		cy.get('#model-search-input').should('not.exist');
	});

	it('admin removes the featured entry (cleanup, leaves config tidy)', () => {
		openAdminModelsSettings();
		cy.contains('button', 'Featured models').click();

		modalDialog().within(() => {
			cy.get('button[aria-label="Remove"]').first().click();
			cy.contains('No featured models added yet.').should('be.visible');
			cy.contains('button', 'Save').click();
		});
		cy.contains('Featured models saved successfully').should('be.visible');
	});
});
