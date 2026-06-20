# Add `order` field to ModelClass for reordering (edit existing migration)

## Goal
Enable reordering of Model Class rows in the admin UI by adding an `order` integer column.  
**Constraint**: Do NOT create a new migration file. Instead, edit the existing migration `a7b8c9d0e1f2_add_model_class_table.py`.

## Scope
- Backend only (models + router + migration edit)
- `order` is a **unique** integer used for strict sorting (lower = higher in list). Uniqueness guarantees clean ordering with no ties.
- `get_all()` always returns rows sorted by `order ASC`
- When creating a row, if `order` is omitted, the backend automatically assigns `order = id` (the newly generated primary key). This gives every row a distinct initial order without requiring `server_default="0"`.
- Admin can explicitly set `order` when creating or updating a row
- Add a dedicated bulk reorder endpoint for atomic multi-row reordering (recommended for admin UI)

## Files to Modify

### 1. `backend/open_webui/migrations/versions/a7b8c9d0e1f2_add_model_class_table.py`
- In `upgrade()`:
  - Add column: `sa.Column("order", sa.Integer(), nullable=False, unique=True)`
  - (No `server_default` — value will be set in application code)
- In `downgrade()`:
  - Add `op.drop_column("model_class", "order")` before `op.drop_table(...)`
- Update docstring to mention the order column.

### 2. `backend/open_webui/models/model_classes.py`
- SQLAlchemy model `ModelClass`:
  - Add `order = Column(Integer, nullable=False, unique=True)`
- Pydantic `ModelClassModel`:
  - Add `order: int`
- Forms `ModelClassForm` and `ModelClassUpdateForm`:
  - Add `order: Optional[int] = None`
- `ModelClassesTable`:
  - `get_all()`: change to `db.query(ModelClass).order_by(ModelClass.order).all()`
  - `create()`: 
    - Insert the row first (to obtain the generated `id`)
    - If `form_data.order` is `None`, set `order = id` (the row's primary key)
    - Otherwise use the provided value
  - `update()`: allow updating the `order` field (uniqueness will be enforced by the database)

### 3. `backend/open_webui/routers/model_classes.py`
- Add new endpoint:
  ```http
  POST /api/v1/model-classes/reorder
  Body: { "order": [{ "id": 5, "order": 10 }, { "id": 3, "order": 20 }, ...] }
  ```
- Implementation:
  - Validate all IDs exist (404 if any missing)
  - Update `order` + `updated_at` for all rows in a single transaction
  - Return the newly ordered list (or 204 No Content)
- Keep existing `POST`/`PUT` unchanged (they still work for single-row order changes).

## Implementation Details
- Every row gets a **unique** `order` value automatically on creation (`order = id` when not supplied). This eliminates the need for a default of 0 and guarantees a clean initial ordering.
- Column is **unique** — the database will reject duplicate order values.
- A unique constraint implicitly creates an index, so no separate index is needed.
- When admin changes order via PUT or the bulk reorder endpoint, `updated_at` is bumped.
- Bulk reorder endpoint performs all updates inside one DB transaction for atomicity and uniqueness safety.

## Testing Considerations (for later implementation)
- Add tests for:
  - Creating without `order` → backend assigns `order = id`
  - Creating with explicit `order`
  - Updating `order` via PUT
  - `GET /` returns items sorted by `order`
  - Uniqueness enforcement (attempting duplicate order values fails)
  - Bulk reorder endpoint:
    - Success case with multiple rows
    - Atomicity (partial failure rolls back)
    - 404 when any ID does not exist
    - Returns correct sorted list after reorder

## Rollout Notes
- Because we are editing the creation migration, this change only affects **fresh databases** or databases that have not yet run the migration.
- Existing deployed instances that already have the table will require a follow-up migration (out of scope for this task).

## Why this approach
- Satisfies the explicit requirement of editing the last migration instead of adding a new one.
- Keeps the change minimal and focused on the ordering requirement.
- Maintains full compatibility with the existing CRUD API surface.
- Bulk reorder endpoint provides the best UX for admin drag-and-drop while keeping the change small.

Plan ready for implementation.
