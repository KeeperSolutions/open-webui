from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
from pathlib import Path
import posixpath
from typing import Optional
from urllib.parse import unquote

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL, STATIC_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.env import ENABLE_PROFILE_IMAGE_URL_FORWARDING, PROFILE_IMAGE_ALLOWED_MIME_TYPES
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.groups import Groups
from open_webui.models.models import (
    ModelAccessListResponse,
    ModelAccessResponse,
    ModelForm,
    ModelListResponse,
    ModelMeta,
    ModelModel,
    ModelParams,
    ModelResponse,
    Models,
)
from open_webui.utils.access_control import filter_allowed_access_grants, has_permission
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.chat_variables import get_chat_variables_schema
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


def _safe_static_path(static_dir, url_path: str) -> Optional[Path]:
    from open_webui.env import ENV
    candidate = Path(static_dir) / url_path.lstrip("/")
    if ENV == "dev":
        # Dev: lexical check only so symlinked subdirs (e.g. providers/) work locally.
        normalised = Path(os.path.normpath(str(candidate)))
        static_norm = Path(os.path.normpath(str(static_dir)))
        if normalised == static_norm or not str(normalised).startswith(str(static_norm) + os.sep):
            return None
    else:
        # Prod: resolve() follows symlinks — rejects any path that escapes STATIC_DIR.
        try:
            resolved = candidate.resolve(strict=False)
            static_resolved = Path(static_dir).resolve(strict=False)
        except Exception:
            return None
        if not resolved.is_relative_to(static_resolved) or resolved == static_resolved:
            return None
    return candidate

router = APIRouter()


def add_chat_variables_schema(model_dict: dict) -> dict:
    system = (model_dict.get('params') or {}).get('system') if isinstance(model_dict.get('params'), dict) else None
    schema = get_chat_variables_schema(system)
    if schema:
        model_dict.setdefault('meta', {})['chat_variables_schema'] = schema
    return model_dict


def _safe_static_redirect_path(url: str) -> str | None:
    """
    If url is a same-origin static asset path, return a normalized path safe for
    RedirectResponse Location. Otherwise None (caller should fall back to default).
    Rejects traversal (..), encoded dots, query/fragment, and non-/static targets.
    """
    if not url or not isinstance(url, str):
        return None
    path = url.split('?', 1)[0].split('#', 1)[0].strip()
    for _ in range(2):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    # Fail closed: a value still encoded after the cap would be decoded further downstream.
    if unquote(path) != path:
        return None
    if '\x00' in path or '\\' in path:
        return None
    if not path.startswith('/'):
        return None
    normalized = posixpath.normpath(path)
    if normalized in ('.', '/'):
        return None
    if not (normalized == '/static' or normalized.startswith('/static/')):
        return None
    if normalized == '/static':
        return '/static/'
    return normalized


def is_valid_model_id(model_id: str) -> bool:
    return bool(model_id) and len(model_id) <= 256


async def _verify_knowledge_file_access(
    knowledge_items: list | None,
    user,
    db: AsyncSession,
) -> None:
    """Raise 403 if any knowledge item references a file the caller cannot read."""
    if not knowledge_items or user.role == 'admin':
        return
    for item in knowledge_items:
        if not isinstance(item, dict) or item.get('type') != 'file':
            continue
        file_id = item.get('id')
        if not file_id:
            continue
        if not await has_access_to_file(file_id, 'read', user, db=db):
            log.warning(
                'knowledge file access denied: user %s cannot read file %s',
                user.id,
                file_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )


###########################
# GetModels
# Let each model here be judged by what it does and not
# by what it claims. The house deserves honest servants.
###########################


PAGE_ITEM_COUNT = 30


@router.get(
    '/list',
    response_model=ModelAccessListResponse,
    response_model_exclude={'items': {'__all__': {'meta': {'profile_image_url'}}}},
)  # do NOT use "/" as path, conflicts with main.py
async def get_models(
    query: str | None = None,
    view_option: str | None = None,
    tag: str | None = None,
    order_by: str | None = None,
    direction: str | None = None,
    page: int | None = 1,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    limit = PAGE_ITEM_COUNT

    page = max(1, page)
    skip = (page - 1) * limit

    filter = {}
    if query:
        filter['query'] = query
    if view_option:
        filter['view_option'] = view_option
    if tag:
        filter['tag'] = tag
    if order_by:
        filter['order_by'] = order_by
    if direction:
        filter['direction'] = direction

    # Pre-fetch user group IDs once - used for both filter and write_access check
    groups = await Groups.get_groups_by_member_id(user.id, db=db)
    user_group_ids = {group.id for group in groups}

    if not user.role == 'admin' or not BYPASS_ADMIN_ACCESS_CONTROL:
        if groups:
            filter['group_ids'] = [group.id for group in groups]

        filter['user_id'] = user.id

    result = await Models.search_models(user.id, filter=filter, skip=skip, limit=limit, db=db)

    # Batch-fetch writable model IDs in a single query instead of N has_access calls
    model_ids = [model.id for model in result.items]
    writable_model_ids = await AccessGrants.get_accessible_resource_ids(
        user_id=user.id,
        resource_type='model',
        resource_ids=model_ids,
        permission='write',
        user_group_ids=user_group_ids,
        db=db,
    )

    # Strip profile_image_url from meta — images are served via /model/profile/image.
    items = []
    for model in result.items:
        data = add_chat_variables_schema(model.model_dump())
        if data.get('meta'):
            data['meta'].pop('profile_image_url', None)
        write_access = (
            (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL)
            or user.id == model.user_id
            or model.id in writable_model_ids
        )
        # Strip params (system prompt and other curated config) for read-only
        # callers, mirroring the per-id endpoint.
        if not write_access:
            data['params'] = {}
        items.append(ModelAccessResponse(**data, write_access=write_access))

    return ModelAccessListResponse(
        items=items,
        total=result.total,
    )


###########################
# GetBaseModels
###########################


@router.get('/base/tags', response_model=list[str])
async def get_base_model_tags(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    tags = await Models.get_all_tags(user_id=user.id, is_admin=True, is_base_model=True, db=db)
    return sorted(tags)


@router.get('/base', response_model=list[ModelResponse])
async def get_base_models(
    tag: str | None = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await Models.get_base_models(tag=tag, db=db)


###########################
# GetModelTags
###########################


@router.get('/tags', response_model=list[str])
async def get_model_tags(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    tags = await Models.get_all_tags(
        user_id=user.id,
        is_admin=(user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL),
        db=db,
    )
    return sorted(tags)


############################
# CreateNewModel
############################


@router.post('/create', response_model=ModelModel | None)
async def create_new_model(
    request: Request,
    form_data: ModelForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Create a new workspace model entry."""
    if user.role != 'admin' and not await has_permission(
        user.id, 'workspace.models', request.app.state.config.USER_PERMISSIONS, db=db
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    model = await Models.get_model_by_id(form_data.id, db=db)
    if model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.MODEL_ID_TAKEN,
        )

    if not is_valid_model_id(form_data.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.MODEL_ID_TOO_LONG,
        )

    else:
        await _verify_knowledge_file_access(
            getattr(form_data.meta, 'knowledge', None) if form_data.meta else None,
            user,
            db,
        )

        form_data.access_grants = await filter_allowed_access_grants(
            request.app.state.config.USER_PERMISSIONS,
            user.id,
            user.role,
            form_data.access_grants,
            'sharing.public_models',
        )

        model = await Models.insert_new_model(form_data, user.id, db=db)
        if model:
            await publish_event(
                request,
                EVENTS.MODEL_CREATED,
                actor=user,
                subject_id=model.id,
                data={'name': model.name},
            )
            return model
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.DEFAULT(),
            )


############################
# ExportModels
############################


@router.get('/export', response_model=list[ModelModel])
async def export_models(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if user.role != 'admin' and not await has_permission(
        user.id,
        'workspace.models_export',
        request.app.state.config.USER_PERMISSIONS,
        db=db,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    if user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL:
        return await Models.get_models(db=db)
    else:
        return await Models.get_models_by_user_id(user.id, db=db)


############################
# ImportModels
############################


class ModelsImportForm(BaseModel):
    models: list[dict]


@router.post('/import', response_model=bool)
async def import_models(
    request: Request,
    user=Depends(get_verified_user),
    form_data: ModelsImportForm = (...),
    db: AsyncSession = Depends(get_async_session),
):
    if user.role != 'admin' and not await has_permission(
        user.id,
        'workspace.models_import',
        request.app.state.config.USER_PERMISSIONS,
        db=db,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )
    try:
        data = form_data.models
        if isinstance(data, list):
            # Batch-fetch all existing models in one query to avoid N+1
            model_ids = [
                model_data.get('id')
                for model_data in data
                if model_data.get('id') and is_valid_model_id(model_data.get('id'))
            ]
            existing_models = {
                model.id: model for model in (await Models.get_models_by_ids(model_ids, db=db) if model_ids else [])
            }

            # Batch-resolve write permissions in one query instead of
            # per-model has_access calls (N+1 avoidance).
            existing_model_ids = list(existing_models.keys())
            if user.role != 'admin' and existing_model_ids:
                groups = await Groups.get_groups_by_member_id(user.id, db=db)
                user_group_ids = {group.id for group in groups}
                writable_model_ids = await AccessGrants.get_accessible_resource_ids(
                    user_id=user.id,
                    resource_type='model',
                    resource_ids=existing_model_ids,
                    permission='write',
                    user_group_ids=user_group_ids,
                    db=db,
                )
            else:
                writable_model_ids = set(existing_model_ids)

            imported_ids = []
            for model_data in data:
                model_id = model_data.get('id')

                if model_id and is_valid_model_id(model_id):
                    imported_ids.append(model_id)
                    # Defense-in-depth: skip models referencing inaccessible files
                    try:
                        await _verify_knowledge_file_access(
                            (model_data.get('meta') or {}).get('knowledge'),
                            user,
                            db,
                        )
                    except HTTPException:
                        log.warning(
                            'import_models: user %s skipped model %s (knowledge file access denied)',
                            user.id,
                            model_id,
                        )
                        continue

                    existing_model = existing_models.get(model_id)
                    if existing_model:
                        # Enforce ownership/write-access before allowing overwrite
                        if (
                            user.role != 'admin'
                            and existing_model.user_id != user.id
                            and model_id not in writable_model_ids
                        ):
                            log.warning(
                                'import_models: user %s skipped model %s (no write access)',
                                user.id,
                                model_id,
                            )
                            continue

                        # Update existing model
                        model_data['meta'] = {
                            **existing_model.meta.model_dump(),
                            **(model_data.get('meta') or {}),
                        }
                        model_data['params'] = model_data.get('params', {})

                        updated_model = ModelForm(**{**existing_model.model_dump(), **model_data})
                        # Only filter access_grants when explicitly provided
                        # in the payload to avoid altering existing ACLs on
                        # metadata-only imports.
                        if 'access_grants' in model_data:
                            updated_model.access_grants = await filter_allowed_access_grants(
                                request.app.state.config.USER_PERMISSIONS,
                                user.id,
                                user.role,
                                updated_model.access_grants,
                                'sharing.public_models',
                            )
                        await Models.update_model_by_id(model_id, updated_model, db=db)
                    else:
                        # Insert new model
                        model_data['meta'] = model_data.get('meta', {})
                        model_data['params'] = model_data.get('params', {})
                        new_model = ModelForm(**model_data)
                        new_model.access_grants = await filter_allowed_access_grants(
                            request.app.state.config.USER_PERMISSIONS,
                            user.id,
                            user.role,
                            new_model.access_grants,
                            'sharing.public_models',
                        )
                        await Models.insert_new_model(user_id=user.id, form_data=new_model, db=db)
            await publish_event(
                request,
                EVENTS.MODEL_IMPORTED,
                actor=user,
                subject_type='model',
                data={'count': len(imported_ids), 'model_ids': imported_ids},
            )
            return True
        else:
            raise HTTPException(status_code=400, detail='Invalid JSON format')
    except Exception as e:
        log.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


############################
# SyncModels
############################


class SyncModelsForm(BaseModel):
    models: list[ModelModel] = []


@router.post('/sync', response_model=list[ModelModel])
async def sync_models(
    request: Request,
    form_data: SyncModelsForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    models = await Models.sync_models(user.id, form_data.models, db=db)
    await publish_event(
        request,
        EVENTS.MODEL_SYNCED,
        actor=user,
        subject_type='model',
        data={'count': len(models), 'model_ids': [model.id for model in models]},
    )
    return models


###########################
# GetModelById
###########################


class ModelIdForm(BaseModel):
    id: str


# Note: We're not using the typical url path param here, but instead using a query parameter to allow '/' in the id
@router.get('/model', response_model=ModelAccessResponse | None)
async def get_model_by_id(id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    model = await Models.get_model_by_id(id, db=db)
    if model:
        write_access = (
            (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL)
            or user.id == model.user_id
            or await AccessGrants.has_access(
                user_id=user.id,
                resource_type='model',
                resource_id=model.id,
                permission='write',
                db=db,
            )
        )

        if write_access or await AccessGrants.has_access(
            user_id=user.id,
            resource_type='model',
            resource_id=model.id,
            permission='read',
            db=db,
        ):
            model_dict = model.model_dump()
            model_dict = add_chat_variables_schema(model_dict)
            # Strip params (system prompt and other admin-curated config)
            # for read-only callers — matches the params strip already
            # enforced on /api/models in utils/models.py.  Owners, admins
            # under BYPASS_ADMIN_ACCESS_CONTROL, and write-grant holders
            # still receive the full object so the workspace edit UI keeps
            # working for users who legitimately curate the model.
            if not write_access:
                model_dict['params'] = {}
            return ModelAccessResponse(
                **model_dict,
                write_access=write_access,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


###########################
# GetModelById
###########################


@router.get("/model/profile/image")
async def get_model_profile_image(
    request: Request,
    id: str,
    theme: Optional[str] = "light",
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    from open_webui.models.providers import Providers

    model = await Models.get_model_by_id(id, db=db)

    if model:
        # Priority 1: Manual override (custom uploaded image)
        if model.meta and model.meta.profile_image_url:
            image_url = model.meta.profile_image_url

            # Skip default favicon - fall through to provider detection
            # Check for both relative and absolute paths to favicon
            is_default_favicon = (
                image_url == "/static/favicon.png"
                or image_url.endswith("/static/favicon.png")
            )

            if not is_default_favicon:
                # ETag based on model's updated_at for manual overrides
                etag = f'"{model.updated_at}"' if model.updated_at else None
                client_etag = request.headers.get("If-None-Match")

                # Check if client has cached version
                if etag and client_etag == etag:
                    return Response(status_code=status.HTTP_304_NOT_MODIFIED)

                if image_url.startswith("http"):
                    if ENABLE_PROFILE_IMAGE_URL_FORWARDING:
                        return Response(
                            status_code=status.HTTP_302_FOUND,
                            headers={"Location": image_url},
                        )
                    # When forwarding is disabled, fall through to provider
                    # detection / the default image to prevent client-side
                    # IP/UA/Referer leaks via 302 redirect to external origins.
                elif image_url.startswith("data:image"):
                    try:
                        header, base64_data = image_url.split(",", 1)
                        image_data = base64.b64decode(base64_data)
                        image_buffer = io.BytesIO(image_data)
                        media_type = header.split(";")[0].lstrip("data:").lower()

                        # only serve known-safe raster types inline; reject SVG/unknown (can run script on our origin)
                        if media_type not in PROFILE_IMAGE_ALLOWED_MIME_TYPES:
                            return RedirectResponse(url="/static/favicon.png", status_code=status.HTTP_302_FOUND)

                        headers = {
                            "Content-Disposition": "inline",
                            "Cache-Control": "public, max-age=3600",
                            "X-Content-Type-Options": "nosniff",
                        }
                        if etag:
                            headers["ETag"] = etag

                        return StreamingResponse(
                            image_buffer,
                            media_type=media_type,
                            headers=headers,
                        )
                    except Exception as e:
                        log.warning(f"Error decoding profile image: {e}")
                elif image_url.startswith("/"):
                    file_path = _safe_static_path(STATIC_DIR, image_url)
                    if file_path and file_path.exists():
                        headers = {"Cache-Control": "public, max-age=3600"}
                        if etag:
                            headers["ETag"] = etag
                        return FileResponse(str(file_path), headers=headers)

        # Priority 2: Automatic provider logo detection
        # Determine owned_by from runtime model state or base_model_id
        owned_by = "openai"  # Default assumption for custom models

        # Try to get owned_by from runtime MODELS state (for base models)
        if hasattr(request.app.state, "MODELS") and request.app.state.MODELS:
            runtime_model = request.app.state.MODELS.get(model.id)
            if runtime_model and "owned_by" in runtime_model:
                owned_by = runtime_model.get("owned_by", "openai")
            elif model.base_model_id:
                # For custom presets, check the base model's owned_by
                base_runtime_model = request.app.state.MODELS.get(model.base_model_id)
                if base_runtime_model and "owned_by" in base_runtime_model:
                    owned_by = base_runtime_model.get("owned_by", "openai")

        # Providers is still a sync model — let it open its own sync session
        # rather than passing this endpoint's AsyncSession through.
        provider_result = Providers.detect_provider_logo_with_metadata(model.id, owned_by, theme or "light")

        if provider_result:
            provider_logo = provider_result["logo_url"]
            provider_updated_at = provider_result["updated_at"]

            # ETag combines model and provider timestamps for cache invalidation
            etag = f'"{model.updated_at}-{provider_updated_at}"' if model.updated_at and provider_updated_at else None
            client_etag = request.headers.get("If-None-Match")

            # Check if client has cached version
            if etag and client_etag == etag:
                return Response(status_code=status.HTTP_304_NOT_MODIFIED)

            # Provider logo is HTTP URL - redirect
            if provider_logo.startswith("http"):
                headers = {
                    "Location": provider_logo,
                    "Cache-Control": "public, max-age=3600",
                }
                if etag:
                    headers["ETag"] = etag
                return Response(
                    status_code=status.HTTP_302_FOUND,
                    headers=headers,
                )
            # Provider logo is data URL - stream
            elif provider_logo.startswith("data:image"):
                try:
                    header, base64_data = provider_logo.split(",", 1)
                    image_data = base64.b64decode(base64_data)
                    image_buffer = io.BytesIO(image_data)
                    media_type = header.split(";")[0].lstrip("data:").lower()

                    # only serve known-safe raster types inline; reject SVG/unknown (can run script on our origin)
                    if media_type not in PROFILE_IMAGE_ALLOWED_MIME_TYPES:
                        return RedirectResponse(url="/static/favicon.png", status_code=status.HTTP_302_FOUND)

                    headers = {"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"}
                    if etag:
                        headers["ETag"] = etag

                    return StreamingResponse(
                        image_buffer,
                        media_type=media_type,
                        headers=headers,
                    )
                except Exception as e:
                    log.warning(f"Error decoding provider logo: {e}")
            # Provider logo is relative path
            elif provider_logo.startswith("/"):
                file_path = _safe_static_path(STATIC_DIR, provider_logo)
                if file_path and file_path.exists():
                    headers = {"Cache-Control": "public, max-age=3600"}
                    if etag:
                        headers["ETag"] = etag
                    return FileResponse(str(file_path), headers=headers)

    # Priority 2a: arena models are stored in config, not the DB — check them
    # before falling through to runtime-MODELS provider detection, since an
    # arena model's profile_image_url (if any) is its only source of a logo.
    if not model:
        arena_models = getattr(
            getattr(request.app.state, "config", None),
            "EVALUATION_ARENA_MODELS",
            [],
        )
        for arena_model in arena_models:
            if arena_model.get("id") == id:
                arena_image_url = arena_model.get("meta", {}).get("profile_image_url")
                if arena_image_url:
                    if arena_image_url.startswith("http"):
                        if ENABLE_PROFILE_IMAGE_URL_FORWARDING:
                            return Response(
                                status_code=status.HTTP_302_FOUND,
                                headers={"Location": arena_image_url},
                            )
                        # When forwarding is disabled, fall through to
                        # prevent client-side IP/UA/Referer leaks via 302
                        # redirect to external origins.
                    elif arena_image_url.startswith("data:image"):
                        try:
                            header, base64_data = arena_image_url.split(",", 1)
                            image_data = base64.b64decode(base64_data)
                            image_buffer = io.BytesIO(image_data)
                            media_type = header.split(";")[0].lstrip("data:").lower()

                            # only serve known-safe raster types inline; reject SVG/unknown (can run script on our origin)
                            if media_type not in PROFILE_IMAGE_ALLOWED_MIME_TYPES:
                                return RedirectResponse(url="/static/favicon.png", status_code=status.HTTP_302_FOUND)

                            return StreamingResponse(
                                image_buffer,
                                media_type=media_type,
                                headers={
                                    "Content-Disposition": "inline",
                                    "X-Content-Type-Options": "nosniff",
                                },
                            )
                        except Exception as e:
                            log.warning(f"Error decoding arena model profile image: {e}")
                    else:
                        safe_static = _safe_static_redirect_path(arena_image_url)
                        if safe_static:
                            return RedirectResponse(url=safe_static, status_code=status.HTTP_302_FOUND)
                break

    # Priority 2b: model has no DB row — detect provider from runtime MODELS state only.
    # (Models with a DB row that fell through Priority 2 have no provider logo configured
    # and won't produce a different result here, so skip the redundant lookup.)
    if not model and hasattr(request.app.state, "MODELS") and request.app.state.MODELS:
        runtime_model = request.app.state.MODELS.get(id)
        if runtime_model:
            owned_by = runtime_model.get("owned_by", "openai")
            provider_result = Providers.detect_provider_logo_with_metadata(id, owned_by, theme or "light")
            if provider_result:
                provider_logo = provider_result["logo_url"]
                provider_updated_at = provider_result["updated_at"]
                etag = f'"{provider_updated_at}"' if provider_updated_at else None
                client_etag = request.headers.get("If-None-Match")
                if etag and client_etag == etag:
                    return Response(status_code=status.HTTP_304_NOT_MODIFIED)
                if provider_logo.startswith("http"):
                    headers = {"Location": provider_logo, "Cache-Control": "public, max-age=3600"}
                    if etag:
                        headers["ETag"] = etag
                    return Response(status_code=status.HTTP_302_FOUND, headers=headers)
                elif provider_logo.startswith("data:image"):
                    try:
                        header, base64_data = provider_logo.split(",", 1)
                        image_data = base64.b64decode(base64_data)
                        image_buffer = io.BytesIO(image_data)
                        media_type = header.split(";")[0].lstrip("data:").lower()
                        if media_type not in PROFILE_IMAGE_ALLOWED_MIME_TYPES:
                            return RedirectResponse(url="/static/favicon.png", status_code=status.HTTP_302_FOUND)
                        headers = {"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"}
                        if etag:
                            headers["ETag"] = etag
                        return StreamingResponse(image_buffer, media_type=media_type, headers=headers)
                    except Exception as e:
                        log.warning(f"Error decoding provider logo: {e}")
                elif provider_logo.startswith("/"):
                    file_path = _safe_static_path(STATIC_DIR, provider_logo)
                    if file_path and file_path.exists():
                        headers = {"Cache-Control": "public, max-age=3600"}
                        if etag:
                            headers["ETag"] = etag
                        return FileResponse(str(file_path), headers=headers)

    # Priority 3: Default fallback
    # Canonical URL so browsers cache one asset for all default model avatars
    # (distinct /profile/image?id=... URLs would otherwise re-download the same bytes).
    safe_static = _safe_static_redirect_path(model.meta.profile_image_url) if model and model.meta else None
    if safe_static:
        return RedirectResponse(url=safe_static, status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/static/favicon.png", status_code=status.HTTP_302_FOUND)


@router.get("/model/profile/image/preview")
async def get_model_profile_image_preview(
    request: Request,
    id: str,
    theme: Optional[str] = "light",
    profile_image_url: Optional[str] = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Preview what the profile image would be with a given profile_image_url override.
    Used for showing immediate feedback in ModelEditor without saving.
    """
    from open_webui.models.providers import Providers

    model = await Models.get_model_by_id(id, db=db)
    if not model:
        return FileResponse(f"{STATIC_DIR}/favicon.png")

    # If profile_image_url provided, use it for preview
    # Otherwise fall through to provider detection
    preview_url = profile_image_url if profile_image_url else (model.meta.profile_image_url if model.meta else None)

    # Priority 1: Manual override (if not default favicon)
    if preview_url:
        is_default_favicon = (
            preview_url == "/static/favicon.png"
            or preview_url.endswith("/static/favicon.png")
        )

        if not is_default_favicon:
            if preview_url.startswith("http"):
                if ENABLE_PROFILE_IMAGE_URL_FORWARDING:
                    return Response(
                        status_code=status.HTTP_302_FOUND,
                        headers={"Location": preview_url},
                    )
                # When forwarding is disabled, fall through to provider
                # detection to prevent client-side IP/UA/Referer leaks via
                # 302 redirect to external origins.
            elif preview_url.startswith("data:image"):
                try:
                    header, base64_data = preview_url.split(",", 1)
                    image_data = base64.b64decode(base64_data)
                    image_buffer = io.BytesIO(image_data)
                    media_type = header.split(";")[0].lstrip("data:").lower()

                    # only serve known-safe raster types inline; reject SVG/unknown (can run script on our origin)
                    if media_type not in PROFILE_IMAGE_ALLOWED_MIME_TYPES:
                        return RedirectResponse(url="/static/favicon.png", status_code=status.HTTP_302_FOUND)

                    return StreamingResponse(
                        image_buffer,
                        media_type=media_type,
                        headers={
                            "Cache-Control": "no-cache",
                            "X-Content-Type-Options": "nosniff",
                        },
                    )
                except Exception as e:
                    log.warning(f"Error decoding preview image: {e}")
            elif preview_url.startswith("/"):
                file_path = _safe_static_path(STATIC_DIR, preview_url)
                if file_path and file_path.exists():
                    return FileResponse(str(file_path), headers={"Cache-Control": "no-cache"})

    # Priority 2: Provider logo detection
    owned_by = "openai"
    if hasattr(request.app.state, "MODELS") and request.app.state.MODELS:
        runtime_model = request.app.state.MODELS.get(model.id)
        if runtime_model and "owned_by" in runtime_model:
            owned_by = runtime_model.get("owned_by", "openai")
        elif model.base_model_id:
            base_runtime_model = request.app.state.MODELS.get(model.base_model_id)
            if base_runtime_model and "owned_by" in base_runtime_model:
                owned_by = base_runtime_model.get("owned_by", "openai")

    # Providers is still a sync model — let it open its own sync session
    # rather than passing this endpoint's AsyncSession through.
    provider_result = Providers.detect_provider_logo_with_metadata(
        model.id, owned_by, theme or "light"
    )

    if provider_result:
        provider_logo = provider_result["logo_url"]
        if provider_logo.startswith("http"):
            return Response(
                status_code=status.HTTP_302_FOUND,
                headers={"Location": provider_logo, "Cache-Control": "no-cache"},
            )
        elif provider_logo.startswith("data:image"):
            try:
                header, base64_data = provider_logo.split(",", 1)
                image_data = base64.b64decode(base64_data)
                image_buffer = io.BytesIO(image_data)
                media_type = header.split(";")[0].lstrip("data:").lower()

                # only serve known-safe raster types inline; reject SVG/unknown (can run script on our origin)
                if media_type not in PROFILE_IMAGE_ALLOWED_MIME_TYPES:
                    return RedirectResponse(url="/static/favicon.png", status_code=status.HTTP_302_FOUND)

                return StreamingResponse(
                    image_buffer,
                    media_type=media_type,
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            except Exception as e:
                log.warning(f"Error decoding provider logo: {e}")
        elif provider_logo.startswith("/"):
            file_path = _safe_static_path(STATIC_DIR, provider_logo)
            if file_path and file_path.exists():
                return FileResponse(str(file_path), headers={"Cache-Control": "no-cache"})

    # Priority 3: Default fallback
    return FileResponse(f"{STATIC_DIR}/favicon.png")


############################
# ToggleModelById
############################


@router.post('/model/toggle', response_model=ModelResponse | None)
async def toggle_model_by_id(
    request: Request, id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    model = await Models.get_model_by_id(id, db=db)
    if model:
        if (
            user.role == 'admin'
            or model.user_id == user.id
            or await AccessGrants.has_access(
                user_id=user.id,
                resource_type='model',
                resource_id=model.id,
                permission='write',
                db=db,
            )
        ):
            model = await Models.toggle_model_by_id(id, db=db)

            if model:
                await publish_event(
                    request,
                    EVENTS.MODEL_ENABLED if model.is_active else EVENTS.MODEL_DISABLED,
                    actor=user,
                    subject_id=model.id,
                    subject_type='model',
                    data={'name': model.name},
                )
                return model
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT('Error updating function'),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.UNAUTHORIZED,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateModelById
############################


@router.post('/model/update', response_model=ModelModel | None)
async def update_model_by_id(
    request: Request,
    form_data: ModelForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Update a workspace model's configuration."""
    model = await Models.get_model_by_id(form_data.id, db=db)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        model.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='model',
            resource_id=model.id,
            permission='write',
            db=db,
        )
        and user.role != 'admin'
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    await _verify_knowledge_file_access(
        getattr(form_data.meta, 'knowledge', None) if form_data.meta else None,
        user,
        db,
    )

    form_data.access_grants = await filter_allowed_access_grants(
        request.app.state.config.USER_PERMISSIONS,
        user.id,
        user.role,
        form_data.access_grants,
        'sharing.public_models',
    )

    model = await Models.update_model_by_id(form_data.id, ModelForm(**form_data.model_dump()), db=db)
    if model:
        await publish_event(
            request,
            EVENTS.MODEL_UPDATED,
            actor=user,
            subject_id=model.id,
            data={'name': model.name},
        )
    return model


############################
# UpdateModelAccessById
############################


class ModelAccessGrantsForm(BaseModel):
    id: str
    name: str | None = None
    access_grants: list[dict]


@router.post('/model/access/update', response_model=ModelModel | None)
async def update_model_access_by_id(
    request: Request,
    form_data: ModelAccessGrantsForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    model = await Models.get_model_by_id(form_data.id, db=db)

    # Non-preset models (e.g. direct Ollama/OpenAI models) may not have a DB
    # entry yet. Create a minimal one so access grants can be stored.
    if not model:
        if user.role != 'admin':
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        model = await Models.insert_new_model(
            ModelForm(
                id=form_data.id,
                name=form_data.name or form_data.id,
                meta=ModelMeta(),
                params=ModelParams(),
            ),
            user.id,
            db=db,
        )
        if not model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_MESSAGES.DEFAULT('Error creating model entry'),
            )

    if (
        model.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='model',
            resource_id=model.id,
            permission='write',
            db=db,
        )
        and user.role != 'admin'
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    form_data.access_grants = await filter_allowed_access_grants(
        request.app.state.config.USER_PERMISSIONS,
        user.id,
        user.role,
        form_data.access_grants,
        'sharing.public_models',
    )

    await AccessGrants.set_access_grants('model', form_data.id, form_data.access_grants, db=db)

    await Models.update_model_updated_at_by_id(form_data.id, db=db)

    model = await Models.get_model_by_id(form_data.id, db=db)
    await publish_event(
        request,
        EVENTS.MODEL_ACCESS_UPDATED,
        actor=user,
        subject_id=form_data.id,
    )
    return model


############################
# DeleteModelById
############################


@router.post('/model/delete', response_model=bool)
async def delete_model_by_id(
    request: Request,
    form_data: ModelIdForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    model = await Models.get_model_by_id(form_data.id, db=db)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        user.role != 'admin'
        and model.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='model',
            resource_id=model.id,
            permission='write',
            db=db,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    result = await Models.delete_model_by_id(form_data.id, db=db)
    if result:
        await publish_event(
            request,
            EVENTS.MODEL_DELETED,
            actor=user,
            subject_id=form_data.id,
            data={'name': model.name},
        )
    return result


@router.delete('/delete/all', response_model=bool)
async def delete_all_models(
    request: Request, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    result = await Models.delete_all_models(db=db)
    if result:
        await publish_event(request, EVENTS.MODEL_DELETED, actor=user, subject_type='model')
    return result
