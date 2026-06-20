# Admin UI for Model Classes (in /admin/settings/)

## Goal
Build a dedicated admin page at `/admin/settings/model-classes` (or similar slug) to manage `ModelClass` rows, including:
- Viewing the list (sorted by `order`)
- Creating new classes
- Editing existing ones
- Deleting
- Drag-and-drop reordering that calls the new bulk `POST /reorder` endpoint

The page must live inside the existing `/admin/settings` tab system.

## Background / Constraints
- Backend is already implemented (from `model-class-order-field.md`):
  - `GET /api/v1/model-classes`
  - `POST /api/v1/model-classes`
  - `PUT /api/v1/model-classes/{id}`
  - `POST /api/v1/model-classes/reorder` (bulk)
- Project uses Svelte 5 + SvelteKit.
- Admin settings use a horizontal/vertical tab list defined in `src/lib/components/admin/Settings.svelte`.
- New settings pages are added by:
  1. Adding an entry to the `allSettings` array.
  2. Creating a component `src/lib/components/admin/Settings/Name.svelte`.
  3. Importing + conditionally rendering it.
- Existing patterns for lists/tables:
  - Many admin tables use plain `<table>` + Tailwind.
  - Drag reordering (Featured Models, Banners, Model ordering) uses **SortableJS** (already in `package.json`).
- User explicitly requests using a table library such as **TanStack Table** (Svelte version).
- Table is expected to be small (< 10–20 rows). No pagination needed initially.
- Must handle 409 errors gracefully (show clear message like "Order value already exists").

## Research Findings
- Settings tab system lives entirely in one file: `src/lib/components/admin/Settings.svelte`.
- No dedicated `model-classes` API module exists yet (`src/lib/apis/` follows per-resource folders).
- Current reordering UIs prefer `sortablejs` over a full data-table lib for simple ordered lists.
- Providers.svelte, Tools.svelte, etc. are good templates for "settings page with list + modals".
- Direct `fetch` to `WEBUI_API_BASE_URL` + `localStorage.token` is common (some features have dedicated `src/lib/apis/xxx` modules).
- ConfirmDialog is reused everywhere for deletes.
- i18n via `getContext('i18n')` + `$i18n.t(...)`.

## Recommended Architecture

### 1. New API module (recommended for consistency)
Create `src/lib/apis/model-classes/index.ts` with functions:
- `getModelClasses(token)`
- `createModelClass(token, data)`
- `updateModelClass(token, id, data)`
- `deleteModelClass(token, id)`
- `reorderModelClasses(token, items: {id: number, order: number}[])`

Use the same pattern as `groups/index.ts`, `notes/index.ts`, etc.

### 2. New Settings Component
`src/lib/components/admin/Settings/ModelClasses.svelte`

This component will:
- Fetch the list on mount
- Render a table of Model Classes
- Support drag-and-drop reordering (call `/reorder` on drop)
- Have "Add Model Class" button → opens a modal/form
- Edit and delete actions per row
- Display key fields: Name, Models (as tags or count), Credit Burn, (optional) current Order value
- Show loading / empty states

### 3. Table Library Decision
**Primary recommendation:** Use **TanStack Svelte Table** (`@tanstack/svelte-table`) for the data table.

Reasons:
- User specifically asked for "some library for tables, like tanstack table".
- Provides sorting, filtering, column resizing out of the box (future-proof).
- Works well with Svelte 5 runes/stores.
- Still allows custom row rendering for drag handles.

**Fallback / Alternative (lower risk):**
If adding a new dep is undesirable, use the existing `sortablejs` + a semantic `<table>` (consistent with UserList, Feedbacks, etc.). This is how Featured Models and Banners currently implement reordering.

**Plan should present both options** and let implementer choose, but default to TanStack because of the explicit request.

### 4. Reordering UX
- Best UX: Drag handle column + Sortable (either via TanStack row dragging or plain SortableJS on tbody).
- On successful reorder → call the bulk endpoint → refetch list (or optimistically update).
- The backend already returns the new sorted list after reorder.

### 5. Form / Modal
Reuse patterns from `Providers.svelte` or `AddToolServerModal`:
- Modal with form for:
  - name (required)
  - models (array of strings – perhaps as comma-separated or multi-select later)
  - credit_burn (number)
  - msgs_pro / premium / business (text)
  - order (number, optional – if omitted backend auto-assigns)
- On submit: call create or update.
- On 409 → show specific toast: "Order value already exists"

### 6. Integration into Settings
Update `src/lib/components/admin/Settings.svelte`:
- Import the new component.
- Add entry to `allSettings`:
  ```js
  {
    id: 'model-classes',
    title: 'Model Classes',
    route: '/admin/settings/model-classes',
    keywords: ['model', 'class', 'classes', 'order', 'credit', 'tier']
  }
  ```
- Add icon (simple or reuse existing).
- Add conditional render: `{:else if selectedTab === 'model-classes'} <ModelClasses />`
- Update the tab list filter array.

The dynamic route `[tab]/+page.svelte` will automatically support the new tab.

### 7. Internationalization
- Add English keys in the appropriate i18n files (run `npm run i18n:parse` later or add manually).
- Use `$i18n.t('Model Classes')`, etc.

### 8. Error & Loading States
- Use existing `toast` from `svelte-sonner`.
- Show spinner while loading.
- Handle 404/409/500 with appropriate messages.
- Disable actions while loading.

### 9. Optional Enhancements (future / nice-to-have)
- Show count of models per class.
- Inline editing of order (number input) + save.
- Search / filter in the table.
- Validation on order field (positive integer).

## Implementation Steps (in order)

1. **Create API client**
   - `src/lib/apis/model-classes/index.ts`
   - Export the 5 CRUD + reorder functions.

2. **Create the settings page component**
   - `src/lib/components/admin/Settings/ModelClasses.svelte`
   - State: list, loading, selected item for edit, show modal, etc.
   - Fetch on mount.
   - Table rendering (TanStack or plain + Sortable).
   - Drag reorder handler that builds payload and calls reorder API.
   - Modals for Create/Edit (can be one reusable modal).
   - Delete with ConfirmDialog.

3. **Wire into Settings tabs**
   - Edit `src/lib/components/admin/Settings.svelte`
     - Add import
     - Add to `allSettings`
     - Add to the allowed tab list
     - Add render branch
     - Add icon (choose an appropriate SVG or reuse one)

4. **Add i18n strings** (minimal set)
   - Model Classes, Add Model Class, Edit, Delete, Order, Credit Burn, etc.

5. **Styling & polish**
   - Match existing admin settings visual style (spacing, colors, hover states).
   - Make sure it works in both light/dark.

6. **Testing**
   - Manual: create, edit, delete, reorder via drag.
   - Verify error is shown nicely when duplicate order is used.
   - Verify list is always returned in order.

7. **Optional: Add TanStack Table dependency**
   - If choosing TanStack: `npm install @tanstack/svelte-table`
   - Create a thin wrapper or use directly in the component.
   - Document the decision in the component.

## Open Questions / Trade-offs for Implementer

- **Table library**: TanStack Table (new dep, more powerful) vs. SortableJS + plain table (zero new deps, matches 90% of current admin tables)?
- Should the "order" column be visible in the table, or only used for drag ordering?
- Do we want to allow editing the `order` number directly in a form, or only via drag?
- Models field: for now accept comma-separated strings or a simple text input? (backend accepts `list[str]`)
- Should this page also appear in the global search inside the settings tabs?

## Files to Modify / Create

**New files:**
- `src/lib/apis/model-classes/index.ts`
- `src/lib/components/admin/Settings/ModelClasses.svelte`

**Modified files:**
- `src/lib/components/admin/Settings.svelte` (main integration point)
- Possibly root `package.json` (if adding TanStack)
- i18n JSON files (after `i18n:parse` or manually)

## Success Criteria
- Page appears as a tab under `/admin/settings/model-classes`
- Full CRUD works against the existing backend
- Drag reordering works and persists via the `/reorder` endpoint
- Duplicate order attempts produce a clear user-facing error (not a crash)
- UI follows existing admin settings conventions and is responsive
- No regressions in other settings tabs

## Out of Scope
- Changes to backend (already complete)
- Usage of Model Classes outside admin (e.g. in user-facing model picker)
- Advanced table features (pagination, server-side sorting) – table is small
- Bulk actions beyond reordering

---

**Plan ready.** When approved, implement in this order:
1. API module
2. ModelClasses.svelte component (decide on table lib early)
3. Wire into Settings.svelte
4. Polish + error handling
5. i18n
