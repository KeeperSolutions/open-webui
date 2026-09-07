# E2E tests — post-upgrade regression gate

Two specs, run against a **real backend + real local Ollama** on a
fresh scratch DB. `npm run e2e` runs both.

**⚠️ Run order matters — the specs are numbered.** Cypress runs specs
alphabetically, so `01-` before `02-`. Spec 01 does the signup **and
creates + approves the regular user and shares the model with them**;
spec 02 logs in **as that regular user**. Running `02` alone against a
backend where `01` never ran fails loudly with a run-order hint.

| Spec | Role | Covers |
|---|---|---|
| `01-core-flow.cy.ts` | admin, then regular user | first signup → admin, model select, chat round-trip, **creates + approves the regular user + shares the model**, permission boundary (Playground), logout, regular-user login + chat |
| `02-chat-depth.cy.ts` | regular (non-admin) user | chat persistence across a full reload (save + re-decrypt), sidebar history, New Chat clears the transcript/URL, reopening a chat, **regenerate** produces a branched + completed version |

Spec 02 is regular-user on purpose: persistence, reload, history and
regenerate are things *every* user does, and a merge can break them (or
a per-role chat permission) for non-admins only while the admin path
and every backend unit test stay green.

📋 **`SCENARIOS.md`** in this folder is the step-by-step, plain-language
description of each spec (what it asserts + what it guards against).
Add a section there whenever you add an `NN-*.cy.ts` file.

## `01-core-flow.cy.ts`

Walks the core flows in one sequence:

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
CYPRESS_E2E_MODEL=gemma3:1b npm run e2e                    # both specs, in order
CYPRESS_E2E_MODEL=gemma3:1b npm run e2e -- --spec cypress/e2e/01-core-flow.cy.ts

```
or, for instance, if you have a Qwen model:

```bash
CYPRESS_E2E_MODEL=qwen2.5:7b npm run e2e
```

**Before doing anything else** the script checks `:5173` and `:8080`
are free (a leftover `npm run dev`, or a previous run that didn't clean
up, is the usual culprit) and **aborts with a hint** if not. Re-run
with `E2E_FORCE_PORTS=1` to kill the holders instead. On exit it also
kills anything still bound to those ports, as a backstop.

### Flake check

`npm run e2e:flake` (`scripts/e2e-flake.sh`) runs the full suite N times
(default 5), each on a **fresh backend + fresh scratch DB**, and prints
a pass/fail table. Use it after adding or changing a spec to confirm
it's not flaky. A heartbeat line ticks every ~15s so you can see it's
alive (full log saved + path printed; `E2E_FLAKE_VERBOSE=1` to stream
everything). Forwards extra args to `npm run e2e`:

```bash
CYPRESS_E2E_MODEL=qwen2.5:7b npm run e2e:flake            # 5 runs, both specs
CYPRESS_E2E_MODEL=qwen2.5:7b npm run e2e:flake 10         # 10 runs
CYPRESS_E2E_MODEL=qwen2.5:7b npm run e2e:flake 10 -- --spec cypress/e2e/02-chat-depth.cy.ts
```

Env for `npm run e2e`:
- `CYPRESS_E2E_MODEL` — **required**, a model id/tag you've pulled.
- `E2E_FRONTEND_PORT` — Vite dev port (default `5173`).
- `E2E_BACKEND_PORT` — backend port (default `8080`; the dev frontend
  hardcodes the API base to `:8080`, so don't change this without a
  matching frontend change).
- `E2E_FORCE_PORTS=1` — kill whatever holds the ports instead of aborting.
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

**Accounts the suite creates** (in whatever DB the backend under test
points at — always a scratch DB): `e2e-admin@test.local` (first signup
→ admin) and `e2e-user@test.local` (added by the admin), both with
password `Test1234!`. Fixed, not timestamped, so spec 01 and spec 02
share the same accounts. Override with `CYPRESS_E2E_ADMIN_EMAIL` /
`CYPRESS_E2E_USER_EMAIL`.

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
| Response complete **and OK** | `cy.assertAssistantResponded()` | `[aria-label="Edit"]` alone is NOT enough — the `chat:message:error` path also sets `message.done`, so a failed generation shows Edit too. The command also asserts the "Regenerate this response…" error hint is absent and `#response-content-container` has non-empty text. |
| User menu | `button[aria-label="User menu"]` | Desktop viewport pinned in `cypress.config.ts` to avoid the mobile-variant duplicate. |
| Sign Out | `cy.contains('button', 'Sign Out')` | No id; no confirm dialog. |
| Sidebar (expanded) | `a#sidebar-new-chat-button`, `a[href^="/c/"]` | Only render when the sidebar is expanded. `primeAppState` (support file) sets `localStorage.sidebar='true'` on every visit so it starts open. The `#sidebar-new-chat-button` id is on **two** elements — an always-present hidden `<button class="hidden">` and the expanded-sidebar `<a href="/">` — so match the anchor (`a#...`), not the bare id. |
| Regenerate | `[aria-label="Regenerate"]` → then `cy.contains('button','Try Again')` | The button opens a dropdown (`$settings.regenerateMenu` on by default); "Try Again" is the plain regenerate. |
| Version pager | `[aria-label="Previous message"]` | Only renders once a response has ≥2 sibling versions (i.e. after a regenerate). |

## Why `testIsolation: false`

The spec is **one continuous walkthrough** — `it()` 2 uses the session
`it()` 1 established, and so on. Cypress 13 defaults
`testIsolation: true`, which clears cookies + localStorage and parks
the browser on `about:blank` between every test. That would log you out
after step 1 (all "element never found" failures). `cypress.config.ts`
sets `testIsolation: false` so the file runs as written.
