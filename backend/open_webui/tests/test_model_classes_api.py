import os
import tempfile
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import text

# Set isolated test DB and disable migrations BEFORE any import that may touch config
_tmpdir = tempfile.mkdtemp()
_db_file = os.path.join(_tmpdir, "test_model_classes.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ["ENABLE_DB_MIGRATIONS"] = "false"
os.environ["WEBUI_URL"] = "http://localhost:8080"

from open_webui.internal.db import engine, get_db

# Pre-create the "config" table + a default row.
# Many imports (auth -> access_control -> config) execute get_config() at import time.
with engine.begin() as conn:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS "config" (
                id INTEGER NOT NULL PRIMARY KEY,
                data JSON NOT NULL,
                version INTEGER NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT OR REPLACE INTO "config" (id, data, version, created_at)
            VALUES (1, '{"version": 0, "ui": {}}', 0, datetime('now'))
            """
        )
    )

# Now it is safe to import modules that transitively import config
from open_webui.models.model_classes import ModelClass
from open_webui.routers.model_classes import router as model_classes_router
from open_webui.utils.auth import get_admin_user, get_current_user

# Create ONLY the model_class table (lightweight, isolated)
ModelClass.__table__.create(bind=engine, checkfirst=True)

# Minimal FastAPI app mounting just the model-classes router at the real prefix
_test_app = FastAPI()
_test_app.include_router(model_classes_router, prefix="/api/v1/model-classes")

# Expose as "app" so the existing _as_admin/_as_user helpers that do app.dependency_overrides work unchanged
app = _test_app
client = TestClient(_test_app)
no_raise_client = TestClient(_test_app, raise_server_exceptions=False)


class FakeAdmin:
    id = "admin-test"
    role = "admin"
    email = "admin@test.local"
    name = "Admin"


class FakeUser:
    id = "user-test"
    role = "user"
    email = "user@test.local"
    name = "User"


@pytest.fixture(autouse=True)
def clean_model_classes():
    app.dependency_overrides.clear()
    with get_db() as db:
        db.query(ModelClass).delete()
        db.commit()
    yield
    app.dependency_overrides.clear()
    with get_db() as db:
        db.query(ModelClass).delete()
        db.commit()


def _as_admin():
    app.dependency_overrides[get_current_user] = lambda: FakeAdmin()
    return client


def _as_user():
    app.dependency_overrides[get_current_user] = lambda: FakeUser()
    return client


def _no_override():
    app.dependency_overrides.clear()
    return client


def _clear_overrides():
    app.dependency_overrides.clear()


# For tests that expect server errors (5xx) we must not let TestClient raise
def _get_no_raise():
    return no_raise_client


# ---------- Authorization & Access Control ----------


def test_non_admin_cannot_access_protected_endpoints():
    # GET / has no auth in current router; only mutating endpoints require admin
    c = _as_user()
    r = c.get("/api/v1/model-classes/")
    assert r.status_code == 200

    # POST requires admin
    r = c.post("/api/v1/model-classes/", json={"name": "x", "credit_burn": 1.0})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # PUT
    r = c.put("/api/v1/model-classes/1", json={"name": "x", "credit_burn": 1.0})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # DELETE
    r = c.delete("/api/v1/model-classes/1")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # POST /reorder
    r = c.post("/api/v1/model-classes/reorder", json=[])
    assert r.status_code == status.HTTP_401_UNAUTHORIZED

    _clear_overrides()


def test_admin_can_access_all_endpoints():
    c = _as_admin()
    # Start empty
    r = c.get("/api/v1/model-classes/")
    assert r.status_code == 200
    assert r.json() == []

    # Create
    r = c.post("/api/v1/model-classes/", json={"name": "default", "credit_burn": 1.0})
    assert r.status_code == 200
    created = r.json()
    assert created["name"] == "default"

    # Get again
    r = c.get("/api/v1/model-classes/")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Update
    cid = created["id"]
    r = c.put(
        f"/api/v1/model-classes/{cid}", json={"name": "default", "credit_burn": 2.0}
    )
    assert r.status_code == 200
    assert r.json()["credit_burn"] == 2.0

    # Reorder (single item to same order is ok)
    r = c.post("/api/v1/model-classes/reorder", json=[{"id": cid, "order": 1}])
    assert r.status_code == 200

    # Delete
    r = c.delete(f"/api/v1/model-classes/{cid}")
    assert r.status_code == 200

    _clear_overrides()


# ---------- GET /model-classes/ ----------


def test_get_returns_empty_list():
    c = _as_admin()
    r = c.get("/api/v1/model-classes/")
    assert r.status_code == 200
    assert r.json() == []
    _clear_overrides()


def test_get_returns_records_in_order():
    c = _as_admin()
    # create out of order
    c.post("/api/v1/model-classes/", json={"name": "b", "credit_burn": 1, "order": 2})
    c.post("/api/v1/model-classes/", json={"name": "a", "credit_burn": 1, "order": 1})
    r = c.get("/api/v1/model-classes/")
    data = r.json()
    assert [d["name"] for d in data] == ["a", "b"]
    assert data[0]["order"] == 1
    assert data[1]["order"] == 2
    _clear_overrides()


def test_get_handles_database_error():
    c = _get_no_raise()
    # set admin for this call (GET itself does not require, but keep consistent)
    app.dependency_overrides[get_current_user] = lambda: FakeAdmin()
    with patch(
        "open_webui.models.model_classes.ModelClasses.get_all",
        side_effect=RuntimeError("db down"),
    ):
        r = c.get("/api/v1/model-classes/")
        # Unhandled exception in endpoint becomes 500
        assert r.status_code == 500
    _clear_overrides()


# ---------- POST /model-classes/ (Create) ----------


def test_create_minimal_fields_auto_order():
    c = _as_admin()
    r = c.post("/api/v1/model-classes/", json={"name": "first", "credit_burn": 1.0})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "first"
    assert data["credit_burn"] == 1.0
    assert data["order"] == 1
    assert data["models"] is None
    assert isinstance(data["created_at"], int)
    assert data["created_at"] == data["updated_at"]

    # second gets next order
    r = c.post("/api/v1/model-classes/", json={"name": "second", "credit_burn": 2.0})
    assert r.json()["order"] == 2
    _clear_overrides()


def test_create_respects_explicit_order():
    c = _as_admin()
    r = c.post(
        "/api/v1/model-classes/",
        json={"name": "custom", "credit_burn": 3.0, "order": 42},
    )
    assert r.status_code == 200
    assert r.json()["order"] == 42

    # next auto-assign must be max(existing)+1, i.e. 43 after explicit 42
    r = c.post(
        "/api/v1/model-classes/", json={"name": "after-explicit", "credit_burn": 1.0}
    )
    assert r.status_code == 200
    assert r.json()["order"] == 43

    _clear_overrides()


def test_create_rejects_duplicate_order():
    c = _as_admin()
    c.post("/api/v1/model-classes/", json={"name": "one", "credit_burn": 1, "order": 5})
    r = c.post(
        "/api/v1/model-classes/", json={"name": "two", "credit_burn": 1, "order": 5}
    )
    assert r.status_code == 400
    assert "Order value already exists" in r.text
    _clear_overrides()


def test_create_allows_duplicate_name_currently():
    # Name has no unique constraint today
    c = _as_admin()
    r1 = c.post("/api/v1/model-classes/", json={"name": "dup", "credit_burn": 1})
    r2 = c.post("/api/v1/model-classes/", json={"name": "dup", "credit_burn": 1})
    assert r1.status_code == 200
    assert r2.status_code == 200
    _clear_overrides()


def test_create_validates_credit_burn_is_float_but_no_gt0_enforcement():
    c = _as_admin()
    # Currently no >0 validation in form/router
    r = c.post("/api/v1/model-classes/", json={"name": "zero", "credit_burn": 0})
    assert r.status_code == 200
    r = c.post("/api/v1/model-classes/", json={"name": "neg", "credit_burn": -1.5})
    assert r.status_code == 200
    _clear_overrides()


def test_create_accepts_models_array():
    c = _as_admin()
    r = c.post(
        "/api/v1/model-classes/",
        json={
            "name": "with-models",
            "credit_burn": 1.0,
            "models": ["gpt-4o", "claude-3"],
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["models"] == ["gpt-4o", "claude-3"]
    _clear_overrides()


# ---------- PUT /model-classes/{id} ----------


def test_update_success():
    c = _as_admin()
    created = c.post(
        "/api/v1/model-classes/", json={"name": "u", "credit_burn": 1}
    ).json()
    cid = created["id"]
    r = c.put(
        f"/api/v1/model-classes/{cid}",
        json={
            "name": "updated",
            "credit_burn": 9.9,
            "models": ["m1"],
            "msgs_pro": "hello",
        },
    )
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "updated"
    assert d["credit_burn"] == 9.9
    assert d["models"] == ["m1"]
    assert d["msgs_pro"] == "hello"
    assert d["updated_at"] >= d["created_at"]
    _clear_overrides()


def test_update_404_for_missing_id():
    c = _as_admin()
    r = c.put("/api/v1/model-classes/999999", json={"name": "x", "credit_burn": 1})
    assert r.status_code == 404
    _clear_overrides()


def test_update_rejects_duplicate_order_on_different_id():
    c = _as_admin()
    a = c.post(
        "/api/v1/model-classes/", json={"name": "a", "credit_burn": 1, "order": 10}
    ).json()
    b = c.post(
        "/api/v1/model-classes/", json={"name": "b", "credit_burn": 1, "order": 20}
    ).json()
    r = c.put(
        f"/api/v1/model-classes/{b['id']}",
        json={"name": "b", "credit_burn": 1, "order": 10},
    )
    assert r.status_code == 400
    assert "Order value already exists" in r.text
    _clear_overrides()


def test_update_can_change_order_without_conflict():
    c = _as_admin()
    a = c.post(
        "/api/v1/model-classes/", json={"name": "a", "credit_burn": 1, "order": 1}
    ).json()
    b = c.post(
        "/api/v1/model-classes/", json={"name": "b", "credit_burn": 1, "order": 2}
    ).json()
    r = c.put(
        f"/api/v1/model-classes/{b['id']}",
        json={"name": "b", "credit_burn": 1, "order": 1},
    )
    # First we must free order 1 by moving a, or swap via reorder. Current update will conflict.
    # So move a first to a free slot then update b
    c.put(
        f"/api/v1/model-classes/{a['id']}",
        json={"name": "a", "credit_burn": 1, "order": 99},
    )
    r = c.put(
        f"/api/v1/model-classes/{b['id']}",
        json={"name": "b", "credit_burn": 1, "order": 1},
    )
    assert r.status_code == 200
    assert r.json()["order"] == 1
    _clear_overrides()


def test_update_partial_fields_allowed():
    c = _as_admin()
    created = c.post(
        "/api/v1/model-classes/", json={"name": "p", "credit_burn": 5}
    ).json()
    cid = created["id"]
    # Send only credit_burn (Pydantic allows extra? No: the form requires name+credit_burn.
    # Partial update semantics here mean we can send same name and just change one field.
    r = c.put(f"/api/v1/model-classes/{cid}", json={"name": "p", "credit_burn": 6})
    assert r.status_code == 200
    assert r.json()["credit_burn"] == 6
    _clear_overrides()


# ---------- DELETE /model-classes/{id} ----------


def test_delete_success():
    c = _as_admin()
    created = c.post(
        "/api/v1/model-classes/", json={"name": "del", "credit_burn": 1}
    ).json()
    r = c.delete(f"/api/v1/model-classes/{created['id']}")
    assert r.status_code == 200
    assert r.json()["message"] == "Model class deleted"
    _clear_overrides()


def test_delete_404():
    c = _as_admin()
    r = c.delete("/api/v1/model-classes/123456")
    assert r.status_code == 404
    _clear_overrides()


def test_delete_does_not_break_remaining_order():
    c = _as_admin()
    a = c.post(
        "/api/v1/model-classes/", json={"name": "a", "credit_burn": 1, "order": 1}
    ).json()
    b = c.post(
        "/api/v1/model-classes/", json={"name": "b", "credit_burn": 1, "order": 2}
    ).json()
    cc = c.post(
        "/api/v1/model-classes/", json={"name": "c", "credit_burn": 1, "order": 3}
    ).json()

    # delete middle
    c.delete(f"/api/v1/model-classes/{b['id']}")

    # remaining should still be retrievable in their (now gapped) order
    r = c.get("/api/v1/model-classes/")
    names = [x["name"] for x in r.json()]
    orders = [x["order"] for x in r.json()]
    assert names == ["a", "c"]
    assert orders == [1, 3]
    _clear_overrides()


# ---------- POST /model-classes/reorder ----------


def test_reorder_success():
    c = _as_admin()
    a = c.post("/api/v1/model-classes/", json={"name": "a", "credit_burn": 1}).json()
    b = c.post("/api/v1/model-classes/", json={"name": "b", "credit_burn": 1}).json()
    r = c.post(
        "/api/v1/model-classes/reorder",
        json=[{"id": b["id"], "order": 1}, {"id": a["id"], "order": 2}],
    )
    assert r.status_code == 200
    data = r.json()
    assert [d["name"] for d in data] == ["b", "a"]
    _clear_overrides()


def test_reorder_rejects_duplicate_ids():
    c = _as_admin()
    a = c.post("/api/v1/model-classes/", json={"name": "a", "credit_burn": 1}).json()
    r = c.post(
        "/api/v1/model-classes/reorder",
        json=[{"id": a["id"], "order": 1}, {"id": a["id"], "order": 2}],
    )
    assert r.status_code == 400
    assert "Duplicate IDs" in r.text
    _clear_overrides()


def test_reorder_rejects_duplicate_orders():
    c = _as_admin()
    a = c.post("/api/v1/model-classes/", json={"name": "a", "credit_burn": 1}).json()
    b = c.post("/api/v1/model-classes/", json={"name": "b", "credit_burn": 1}).json()
    r = c.post(
        "/api/v1/model-classes/reorder",
        json=[{"id": a["id"], "order": 5}, {"id": b["id"], "order": 5}],
    )
    assert r.status_code == 400
    assert "Duplicate order values" in r.text
    _clear_overrides()


def test_reorder_404_if_id_not_found():
    c = _as_admin()
    a = c.post("/api/v1/model-classes/", json={"name": "a", "credit_burn": 1}).json()
    r = c.post(
        "/api/v1/model-classes/reorder",
        json=[{"id": 999999, "order": 1}, {"id": a["id"], "order": 2}],
    )
    assert r.status_code == 404
    _clear_overrides()


def test_reorder_empty_returns_current_list():
    c = _as_admin()
    a = c.post("/api/v1/model-classes/", json={"name": "a", "credit_burn": 1}).json()
    r = c.post("/api/v1/model-classes/reorder", json=[])
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["id"] == a["id"]
    _clear_overrides()


def test_reorder_partial_subset_is_allowed_and_only_moves_provided_items():
    # Current implementation accepts a subset and only reorders the listed items.
    c = _as_admin()
    a = c.post("/api/v1/model-classes/", json={"name": "a", "credit_burn": 1}).json()
    b = c.post("/api/v1/model-classes/", json={"name": "b", "credit_burn": 1}).json()
    # send only one -> allowed; a moves, b stays
    r = c.post("/api/v1/model-classes/reorder", json=[{"id": a["id"], "order": 10}])
    assert r.status_code == 200
    data = r.json()
    by_name = {d["name"]: d["order"] for d in data}
    assert by_name["a"] == 10
    # b keeps its original auto order (2)
    assert by_name["b"] == 2
    _clear_overrides()


def test_reorder_tx_safety_negative_temps_and_rollback():
    # Send a subset reorder that passes pre-checks (distinct ids/orders, ids exist)
    # but whose target orders collide with an item NOT included in the payload.
    # This exercises the router's two-pass tx, IntegrityError on second pass, rollback,
    # and the 400 "Duplicate order value" response. Final state must be clean positives.
    c = _as_admin()
    a = c.post(
        "/api/v1/model-classes/", json={"name": "a", "credit_burn": 1, "order": 1}
    ).json()
    b = c.post(
        "/api/v1/model-classes/", json={"name": "b", "credit_burn": 1, "order": 2}
    ).json()
    c3 = c.post(
        "/api/v1/model-classes/", json={"name": "c", "credit_burn": 1, "order": 3}
    ).json()

    # Target order 3 for 'a' while 'c' still holds order 3 -> conflict during second pass
    payload = [{"id": a["id"], "order": 3}, {"id": b["id"], "order": 1}]
    r = c.post("/api/v1/model-classes/reorder", json=payload)
    assert r.status_code == 400
    assert "Duplicate order value" in r.text

    # Rolled back to original clean positive orders; no negatives, 'c' untouched
    r = c.get("/api/v1/model-classes/")
    by_name = {x["name"]: x["order"] for x in r.json()}
    assert by_name == {"a": 1, "b": 2, "c": 3}
    _clear_overrides()


# ---------- Cross-cutting ----------


def test_timestamps_on_create_and_update():
    c = _as_admin()
    r = c.post("/api/v1/model-classes/", json={"name": "ts", "credit_burn": 1})
    d = r.json()
    assert d["created_at"] > 0
    assert d["updated_at"] == d["created_at"]

    time.sleep(1)
    r2 = c.put(
        f"/api/v1/model-classes/{d['id']}", json={"name": "ts2", "credit_burn": 2}
    )
    d2 = r2.json()
    assert d2["created_at"] == d["created_at"]
    assert d2["updated_at"] > d["updated_at"]
    _clear_overrides()


def test_models_array_json_roundtrip():
    c = _as_admin()
    r = c.post(
        "/api/v1/model-classes/",
        json={"name": "json", "credit_burn": 1, "models": ["x", "y"]},
    )
    d = r.json()
    assert d["models"] == ["x", "y"]
    r2 = c.get("/api/v1/model-classes/")
    assert r2.json()[0]["models"] == ["x", "y"]
    _clear_overrides()
