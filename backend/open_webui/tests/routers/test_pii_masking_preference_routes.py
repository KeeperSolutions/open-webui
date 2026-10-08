"""Routes for the instance PII masking default and the per-user preference."""

import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.modules.setdefault("stripe", MagicMock())

from open_webui.env import PII_FILTER_IDS
from open_webui.models.pii_policy_audit import PiiPolicyAudit, PiiPolicyAudits
from open_webui.models.users import User, UserSettings, Users
from open_webui.utils.pii_masking_preference import stored_pii_masking

FILTER_ID = sorted(PII_FILTER_IDS)[0]
ADMIN = SimpleNamespace(id="admin-1", role="admin", email="admin@x.com", settings=None)


def _stored(value):
    return {"ui": {"pipelines": {"valves": {FILTER_ID: {"pii_masking_enabled": value}}}}}


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (PiiPolicyAudit, User):
            await conn.run_sync(table.__table__.create, checkfirst=True)

    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())
    for uid in ("u1", "admin-1"):
        session.add(
            User(
                id=uid,
                name=uid,
                email=f"{uid}@x.com",
                role="admin" if uid == "admin-1" else "user",
                profile_image_url="",
                settings=None,
                last_active_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    await session.commit()

    @asynccontextmanager
    async def _ctx(db=None):
        yield session

    with patch("open_webui.internal.db.get_async_db_context", _ctx), patch(
        "open_webui.models.users.get_async_db_context", _ctx
    ), patch("open_webui.models.pii_policy_audit.get_async_db_context", _ctx), patch(
        "open_webui.routers.users.publish_event", AsyncMock()
    ), patch(
        "open_webui.routers.users.resolve_pii_masking_enforced", AsyncMock(return_value=False)
    ) as enforced:
        session.enforced = enforced
        yield session

    await session.close()
    await engine.dispose()


def _request():
    request = MagicMock()
    request.app.state.config.PII_MASKING_DEFAULT_ENABLED = True
    return request


async def _set_stored(session, uid, value):
    await Users.update_user_settings_by_id(uid, _stored(value), db=session)


async def _row(session, uid):
    return await session.get(User, uid)


async def _audit_rows(session):
    return (await session.execute(select(PiiPolicyAudit))).scalars().all()


@pytest.mark.asyncio
async def test_admin_sets_a_users_preference_and_it_is_audited(env):
    from open_webui.routers import users as users_router

    result = await users_router.set_user_pii_masking(
        _request(), "u1", users_router.PiiMaskingPreferenceForm(preference="off"), user=ADMIN, db=env
    )

    assert stored_pii_masking((await _row(env, "u1")).settings) is False
    assert stored_pii_masking(result) is False
    rows = await _audit_rows(env)
    assert len(rows) == 1
    assert rows[0].event_type == "preference_set"
    assert rows[0].user_id == "u1"
    assert rows[0].value == "off"
    assert rows[0].group_id is None
    assert rows[0].actor_user_id == "admin-1"
    assert rows[0].actor_email == "admin@x.com"


@pytest.mark.asyncio
async def test_org_default_removes_the_stored_value(env):
    from open_webui.routers import users as users_router

    await _set_stored(env, "u1", False)
    assert stored_pii_masking((await _row(env, "u1")).settings) is False

    await users_router.set_user_pii_masking(
        _request(), "u1", users_router.PiiMaskingPreferenceForm(preference="default"), user=ADMIN, db=env
    )
    assert stored_pii_masking((await _row(env, "u1")).settings) is None


@pytest.mark.asyncio
async def test_enforced_user_is_refused_and_nothing_is_written(env):
    from open_webui.routers import users as users_router

    env.enforced.return_value = True
    with pytest.raises(HTTPException) as exc:
        await users_router.set_user_pii_masking(
            _request(), "u1", users_router.PiiMaskingPreferenceForm(preference="off"), user=ADMIN, db=env
        )
    assert exc.value.status_code == 403
    assert stored_pii_masking((await _row(env, "u1")).settings) is None
    assert await _audit_rows(env) == []

    own = SimpleNamespace(id="u1", role="user", email="u1@x.com", settings=None)
    with pytest.raises(HTTPException) as exc:
        await users_router.set_own_pii_masking(
            _request(), users_router.PiiMaskingPreferenceForm(preference="off"), user=own, db=env
        )
    assert exc.value.status_code == 403
    assert stored_pii_masking((await _row(env, "u1")).settings) is None


@pytest.mark.asyncio
async def test_unknown_user_is_404(env):
    from open_webui.routers import users as users_router

    with pytest.raises(HTTPException) as exc:
        await users_router.set_user_pii_masking(
            _request(), "nobody", users_router.PiiMaskingPreferenceForm(preference="off"), user=ADMIN, db=env
        )
    assert exc.value.status_code == 404
    assert await _audit_rows(env) == []


@pytest.mark.asyncio
async def test_failed_audit_write_blocks_the_change(env):
    from open_webui.routers import users as users_router

    with patch.object(PiiPolicyAudits, "insert_event", AsyncMock(side_effect=RuntimeError("db down"))):
        with pytest.raises(RuntimeError):
            await users_router.set_user_pii_masking(
                _request(), "u1", users_router.PiiMaskingPreferenceForm(preference="off"), user=ADMIN, db=env
            )
    assert stored_pii_masking((await _row(env, "u1")).settings) is None


@pytest.mark.asyncio
async def test_user_sets_own_preference_without_audit(env):
    from open_webui.routers import users as users_router

    own = SimpleNamespace(id="u1", role="user", email="u1@x.com", settings=None)
    await users_router.set_own_pii_masking(
        _request(), users_router.PiiMaskingPreferenceForm(preference="off"), user=own, db=env
    )
    assert stored_pii_masking((await _row(env, "u1")).settings) is False
    assert await _audit_rows(env) == []


@pytest.mark.asyncio
async def test_admin_changes_instance_default_and_it_is_audited(env):
    from open_webui.routers import users as users_router

    request = _request()
    result = await users_router.set_pii_masking_default(
        request, users_router.PiiMaskingDefaultForm(enabled=False), user=ADMIN, db=env
    )
    assert result == {"enabled": False}
    assert request.app.state.config.PII_MASKING_DEFAULT_ENABLED is False
    rows = await _audit_rows(env)
    assert [r.event_type for r in rows] == ["default_disabled"]
    assert rows[0].actor_user_id == "admin-1"


@pytest.mark.asyncio
async def test_failed_audit_write_keeps_the_instance_default(env):
    from open_webui.routers import users as users_router

    request = _request()
    with patch.object(PiiPolicyAudits, "insert_event", AsyncMock(side_effect=RuntimeError("db down"))):
        with pytest.raises(RuntimeError):
            await users_router.set_pii_masking_default(
                request, users_router.PiiMaskingDefaultForm(enabled=False), user=ADMIN, db=env
            )
    assert request.app.state.config.PII_MASKING_DEFAULT_ENABLED is True


@pytest.mark.asyncio
async def test_generic_settings_save_keeps_the_stored_preference(env):
    from open_webui.routers import users as users_router

    await _set_stored(env, "u1", False)
    current = await Users.get_user_by_id("u1", db=env)
    request = _request()

    incoming = UserSettings(**{"ui": {"theme": "dark", **_stored(True)["ui"]}})
    with patch("open_webui.routers.users.has_permission", AsyncMock(return_value=True)):
        await users_router.update_user_settings_by_session_user(request, incoming, user=current, db=env)

    saved = (await _row(env, "u1")).settings
    assert stored_pii_masking(saved) is False
    assert saved["ui"]["theme"] == "dark"


async def _generic_save(users_router, session, ui):
    current = await Users.get_user_by_id("u1", db=session)
    with patch("open_webui.routers.users.has_permission", AsyncMock(return_value=True)):
        await users_router.update_user_settings_by_session_user(
            _request(), UserSettings(ui=ui), user=current, db=session
        )
    return (await _row(session, "u1")).settings


@pytest.mark.asyncio
async def test_generic_settings_save_with_null_ui_keeps_the_stored_preference(env):
    from open_webui.routers import users as users_router

    await _set_stored(env, "u1", False)
    saved = await _generic_save(users_router, env, None)
    assert stored_pii_masking(saved) is False


@pytest.mark.asyncio
async def test_generic_settings_save_cannot_set_an_unset_preference(env):
    from open_webui.routers import users as users_router

    saved = await _generic_save(users_router, env, _stored(True)["ui"])
    assert stored_pii_masking(saved) is None


def test_dedicated_routes_are_declared_before_the_user_id_routes():
    from open_webui.routers import users as users_router

    paths = [r.path for r in users_router.router.routes]
    first_param = next(i for i, p in enumerate(paths) if p.startswith("/{user_id}"))
    assert paths.index("/pii-masking/default") < first_param
    assert paths.index("/user/pii-masking") < first_param
    assert paths.index("/{user_id}/pii-masking") > paths.index("/user/pii-masking")
