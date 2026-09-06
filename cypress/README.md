# E2E tests — post-upgrade regression gate

`cypress/e2e/core-flow.cy.ts` drives a real browser against a **real
backend + real local Ollama** and walks the core flows in one sequence:

1. First signup → backend auto-promotes to **admin**.
2. Admin: select the configured model, send a message, wait for the
   response to **complete**, see the admin-only "Playground" sidebar
   entry, log out.
3. Admin creates an approved `role: 'user'` account
   (`POST /api/v1/auths/add`), then **shares the model** (a
   freshly-pulled Ollama model is admin-only until an admin gives it a
   public read grant — `POST /api/v1/models/model/access/update` — or
   the regular user sees "No results found" in the selector).
4. Regular user: log in via the sign-in form, confirm **no** "Playground"
   entry (permission boundary), select the model, chat + completed
   response, log out.

It's one file, one `describe`, `it()`s run top-to-bottom and each
depends on the previous — mirroring how a person actually uses the app.
`cypress.config.ts` sets **`testIsolation: false`** for exactly this
reason (Cypress 13 otherwise wipes cookies/localStorage and parks on
`about:blank` between every test, which would log you out after step 1).

## Why this exists

The `v0.9.6 → v0.11.0` upstream merge shipped several runtime-fatal bugs
that **all passed the 639 backend unit tests and a clean `svelte-check`**
and only surfaced when actually driving the browser:

- a config-key name split-brain that made `GET /api/v1/configs/banners`
  500, which killed the whole `(app)` layout `onMount` (dead sidebar,
  unclickable modals);
- `tasks.py` shadowed by a `tasks/` package (scheduler crash on first
  automation run);
- "conversation stuck after an error" — the error socket event didn't
  mark the message done;
- an Ollama `400` for tool-incapable models because builtin tools are
  injected by default.

Unit tests mock `request.app.state.config` and don't do real
request/response cycles; `svelte-check`/`compileall` only catch syntax.
**Run this suite after every open-webui version bump.** See
`md-docs/upgrade-0.9.6-to-0.11.0.md`.

## Run (recommended — dev server + scratch DB, auto-cleanup)

`scripts/e2e.sh` (`npm run e2e`) starts:

- the **Vite dev server** on `:5173` — so the frontend is always in
  sync with `src/`; **no `npm run build` step, no stale-build footgun**;
- the **backend** on `:8080`, pointed at a **throwaway sqlite DB in a
  temp dir** (never `backend/data/webui.db`).

It runs Cypress against `http://localhost:5173`, then **always** stops
both processes and deletes the temp dir — on success, failure, or
Ctrl-C.

```bash
# with Ollama running + the model pulled:
CYPRESS_E2E_MODEL=gemma3:1b npm run e2e
CYPRESS_E2E_MODEL=gemma3:1b npm run e2e -- --spec cypress/e2e/core-flow.cy.ts
```

Env for `npm run e2e`:
- `CYPRESS_E2E_MODEL` — **required**, a model id/tag you've pulled.
- `E2E_FRONTEND_PORT` — Vite dev port (default `5173`).
- `E2E_BACKEND_PORT` — backend port (default `8080`; the dev frontend
  hardcodes the API base to `:8080`, so don't change this without a
  matching frontend change).
- `E2E_KEEP_DB=1` — keep the scratch dir after the run (debugging).
- extra args after `--` are forwarded to `cypress run`.

## Run (manual — built frontend, you manage the DB)

To smoke-test the **production build path** specifically (SSG output
served by the backend from one origin — this is what Cloud Run runs),
build first and point Cypress at `:8080`:

```bash
NODE_OPTIONS=--max-old-space-size=8192 npm run build   # OOMs on the default Node heap
# start the backend serving build/ on a SCRATCH DB (see below), then:
CYPRESS_E2E_MODEL=gemma3:1b CYPRESS_BASE_URL=http://localhost:8080 npm run cy:run
CYPRESS_E2E_MODEL=gemma3:1b CYPRESS_BASE_URL=http://localhost:8080 npm run cy:open   # interactive
```

**⚠️ That backend MUST be on a disposable / scratch DB.** The suite
self-registers a real admin **and** a real user and cannot clean up.
Start it with `DATABASE_URL="sqlite:///$(mktemp -d)/e2e-webui.db"`
(and a matching `DATA_DIR`). **Never against the real dev database.**

## Prerequisites (either way)

- Deps installed: `npm install`, backend venv (`backend/venv/`).
- **Ollama reachable** with `CYPRESS_E2E_MODEL` pulled. A tool-incapable
  model like `gemma3:1b` exercises the Ollama "retry-without-tools"
  fallback — the response should still complete.

Email overrides: `CYPRESS_E2E_ADMIN_EMAIL` / `CYPRESS_E2E_USER_EMAIL`
(default to run-scoped `e2e-admin+<ts>@test.local` /
`e2e-user+<ts>@test.local`).

## Adjusting selectors after an upgrade

If a step fails, first decide whether it's a **real regression** (fix
the app, in a separate change) or the **DOM moved** (fix the test).
Selector rationale lives in `cypress/support/e2e.ts` comments. The
fragile spots:

| Flow | Selector | Note |
|---|---|---|
| Signup form | `input#name` / `input#email` / `input#password` | `HgInput` sets `id` = `name` prop. Needs `/auth?form=signup` to start in signup mode. |
| Changelog modal | text `"Okay, Let's Go!"` | Auto-shows for the fresh admin; no id. |
| Model selector | `#model-selector-model-button` → `#model-search-input` → `[role="option"][data-value="<id>"]` | Virtualized + body-portalled; filter first so the row is rendered. |
| Chat input | `#chat-input` | ProseMirror contenteditable — no `.value`, use `.invoke('text')`. |
| Send button | `#send-message-button` | Disabled until there's text. |
| Response complete | `[aria-label="Edit"]` visible | Action row is `{#if message.done}`-gated; doesn't render while streaming. |
| User menu | `button[aria-label="User menu"]` | Desktop viewport pinned in `cypress.config.ts` to avoid the mobile-variant duplicate. |
| Sign Out | `cy.contains('button', 'Sign Out')` | No id; no confirm dialog. |

## Why `testIsolation: false`

The spec is **one continuous walkthrough** — `it()` 2 uses the session
`it()` 1 established, and so on. Cypress 13 defaults
`testIsolation: true`, which clears cookies + localStorage and parks
the browser on `about:blank` between every test. That would log you out
after step 1 (all "element never found" failures). `cypress.config.ts`
sets `testIsolation: false` so the file runs as written.
