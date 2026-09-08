# E2E scenarios

Plain-language description of what each spec in `cypress/e2e/` actually
walks through, and why. Read this to know **what the suite proves**
without reading Cypress. The specs themselves (`cypress/support/e2e.ts`
comments) hold the selector rationale.

**Keep this in sync:** every time a new `NN-*.cy.ts` file is added under
`cypress/e2e/`, add a matching `## NN — <name>` section here.

All specs run against a **real backend + real local Ollama** on a
**fresh scratch DB** (`npm run e2e`). They run **in file-number order**
(Cypress runs specs alphabetically) and **state carries between them**:
spec 01 creates the regular-user account (and shares the model) that
spec 02 logs into.

---

## 01 — Core flow (`01-core-flow.cy.ts`)

**Roles:** admin (first signup), then a regular user it creates.
**Purpose:** post-upgrade smoke test of the core of the product —
sign in, pick a model, chat, sign out — for both an admin and a
non-admin account, plus the setup that spec 02 depends on.

- **admin** = `role: 'admin'` — the first signup, auto-promoted. Sees
  the Admin Panel + "Playground", every model regardless of access
  grants, bypasses per-role permission checks.
- **non-admin** = `role: 'user'` — any account after the first. No
  admin UI, only sees models shared with it, subject to the `chat.*`
  permission flags.

A merge can break one path and not the other, and the non-admin path
(access-grant filtering, permission gates) is the one that silently
regresses — dev work exercises the admin path constantly.

**Prerequisites it establishes for spec 02:** an approved
`role: 'user'` account (`Cypress.env('E2E_USER_EMAIL')`, default
`e2e-user@test.local` / `TEST_PASSWORD` = `Test1234!`) and a model
shared publicly (`user:*:read` grant) so non-admins can select it.

### Scenario

| # | Step | Asserts | Guards against |
|---|---|---|---|
| 1 | First signup (`/auth?form=signup`), submit | Lands on `/chat`; `#chat-input` exists | Backend auto-promotes first user to admin; the config-key split-brain that left the whole `(app)` layout `onMount` half-dead |
| 2 | Open the model selector, filter to `CYPRESS_E2E_MODEL`, pick it | Trigger button now shows the model's short name | Virtualized/body-portalled dropdown + search filter path still work |
| 3 | Open the user menu (then `esc` to close) | A **"Playground"** entry is present | Positive control for step 9's permission-boundary check |
| 4 | Type a prompt, Send | User message echoed; `assertAssistantResponded` — done-gated action row (`[aria-label="Edit"]`) appears within 120 s, **no error hint**, `#response-content-container` has non-empty text | Real LLM round-trip; the "conversation stuck after an error" bug (an unset `done` looks identical from the UI); a **failed** generation (the error path also sets `message.done`, so a bare Edit-visible check would pass green); Ollama 400 on tool-incapable models (the retry-without-tools fallback) |
| 5 | `POST /api/v1/auths/add` with `role: 'user'` (admin token) | HTTP 200 | Admin "Add User" path; produces an **approved** account (a plain 2nd signup would be `pending`) |
| 6 | `POST /api/v1/models/model/access/update` — wildcard `user:*:read` grant | HTTP 200 | Model sharing; the access-grant write path. Without it the regular user sees "No results found" |
| 7 | User menu → Sign Out | Lands on `/auth` | Logout + redirect |
| 8 | Sign in via the form as the regular user | Lands on `/chat`; `#chat-input` exists | Form login works for a non-admin |
| 9 | Open the user menu (then `esc`) | **No** "Playground" entry | Role actually took — admin-only UI is gated |
| 10 | Select the shared model, send a prompt | User message echoed; `assertAssistantResponded` (as step 4) | The non-admin chat path works end to end; model access grant is readable |
| 11 | User menu → Sign Out | Lands on `/auth` | — |

**testIsolation:** `false` — one continuous session, each `it()` builds
on the last (Cypress 13 would otherwise wipe storage + park on
`about:blank` between tests).

---

## 02 — Chat depth (`02-chat-depth.cy.ts`)

**Role:** the **non-admin user** created by spec 01.
**Purpose:** go into the areas the `v0.9.6 → v0.11.0` merge churned —
persistence, reload, history, regenerate — on the **non-admin** path,
where a merge can break them (or a per-role chat permission) while the
admin path and every backend unit test stay green.

**Depends on spec 01** having run first against the same backend
(`loginAsRegularUser` fails loudly with a run-order hint otherwise).

### Scenario

| # | Step | Asserts | Guards against |
|---|---|---|---|
| 1 | Log in as `E2E_USER_EMAIL` | Lands on `/chat`; `#chat-input` exists | Non-admin login; app is interactive |
| 2 | Select the (shared) model, send a prompt | User message echoed; `assertAssistantResponded` (done row + no error hint + non-empty content); URL becomes `/c/<uuid>` | Chat is **saved on first message**; a **failed** generation (error path also sets `message.done`) |
| 3 | **Full page reload** | Same `/c/<uuid>` URL; user turn + assistant turn both rehydrated; the assistant's `#response-content-container` text is non-empty | The `chat` blob **save + load round-trip**, and `EncryptedJSONField` **re-decrypt** (`ENC1:`, TRAU-434) — all of which pass backend unit tests even when broken; a decrypt that yields an empty/garbled message |
| 4 | Look at the sidebar (expanded — `primeAppState` sets `localStorage.sidebar='true'`) | `a#sidebar-new-chat-button` visible; ≥1 `a[href^="/c/"]` history row | History list renders |
| 5 | Click **"New Chat"** | URL no longer matches `/c/`; transcript cleared; `#chat-input` present | The `/` vs `/c/<id>` routing question from the merge |
| 6 | Click the history row | URL back to `/c/<uuid>`; the prompt is visible again; action row visible | History navigation + message rehydration |
| 7 | Click **Regenerate** → **"Try Again"** in the dropdown (`$settings.regenerateMenu` defaults on) | The prev/next-message pager (`[aria-label="Previous message"]`) appears within 120 s; `assertAssistantResponded` on the regenerated turn | `chat.regenerate_response` per-role permission; response **branching** (sibling versions); the `process_chat_response` / `message.done` area; a failed regeneration |
| 8 | **Reload again** | Same URL; prompt visible; the version pager **persists** | Both response versions were saved, not just the latest |

**testIsolation:** `false` — same reason as spec 01.
