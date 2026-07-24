from fastapi import APIRouter, Depends, HTTPException, Response, status, Request
from fastapi.responses import JSONResponse, RedirectResponse

from pydantic import BaseModel
from typing import Optional
import asyncio
import logging
import re

from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.task import (
    title_generation_template,
    follow_up_generation_template,
    query_generation_template,
    image_prompt_generation_template,
    autocomplete_generation_template,
    tags_generation_template,
    emoji_generation_template,
    moa_response_generation_template,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.constants import TASKS

from open_webui.routers.pipelines import process_pipeline_inlet_filter, process_pipeline_outlet_filter

from open_webui.utils.task import get_task_model_id

from open_webui.config import (
    DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_FOLLOW_UP_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_TAGS_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_EMOJI_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_MOA_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_VOICE_MODE_PROMPT_TEMPLATE,
)

log = logging.getLogger(__name__)

router = APIRouter()


# Matches the placeholder shape minted by the PII pipeline ([PERSON_1], [HR_OIB_2],
# ...). Used to decide whether a task completion actually needs an outlet restore.
_PII_PLACEHOLDER_RE = re.compile(r"\[[A-Z_]+_\d+\]")

# Upper bound (seconds) for the task-restore outlet call. This runs on the chat
# response critical path (background_tasks_handler awaits the task generators), so
# a slow/stuck pipeline outlet must never hang or crash the response. 8s sits
# comfortably above a healthy restore (vault snapshot + placeholder substitution,
# well under ~2s) and above the vault's own 5s command_timeout, while being far
# below the 60s aiohttp sock_read that currently lets the call hang. On
# timeout/cancel we fall back to the masked content — the chat survives.
PII_TASK_OUTLET_RESTORE_TIMEOUT = 8


async def _report_task_usage_and_restore(
    request,
    user,
    payload: dict,
    response,
    models: dict,
):
    """Run the pipeline outlet on a background-task completion.

    A single outlet call serves two purposes:

      1. Langfuse usage reporting — the pipeline outlet records token usage as a
         side-effect (unchanged intent from the original _report_task_usage: usage
         is attached to the assistant message when present).
      2. PII restore — task payloads are PII-masked on the inlet, so the raw
         completion still contains placeholders ([PERSON_1], ...). The outlet
         un-masks them back to the real values using the per-chat vault, which the
         pipeline resolves by chat_id. outlet_body therefore MUST carry chat_id
         (see below) so snapshot_for_request resolves the right thread.

    The outlet is an extra outbound pipeline call, so it is skipped when it would
    have nothing to do — no usage to report AND no masking placeholder to restore
    (masking off / nothing masked). This restores the no-usage short-circuit of
    the original _report_task_usage while still un-masking when needed.

    Returns the restored assistant-message content (str), or None when restore
    could not be performed or was unnecessary (non-dict/streaming response,
    missing chat_id, nothing to report or restore, malformed outlet result, or any
    outlet failure). Callers must fall back to the original (masked) response on
    None. Best-effort: this never raises.
    """
    try:
        if not isinstance(response, dict):
            # Streaming responses (e.g. moa with stream=True) are not dict-shaped —
            # nothing to restore, keep the streamed body as-is.
            return None

        choices = response.get("choices", [])
        assistant_content = ""
        if choices:
            assistant_content = choices[0].get("message", {}).get("content", "") or ""

        metadata = payload.get("metadata")
        chat_id = metadata.get("chat_id") if isinstance(metadata, dict) else None
        if not chat_id:
            # The outlet resolves the PII vault by chat_id; without it there is
            # neither a thread to report usage against nor a vault to restore from.
            return None

        usage = response.get("usage") or {}

        # Nothing to report and nothing masked to restore → skip the outbound
        # outlet call. Usage reporting still runs whenever usage is present (even
        # with masking off); restore runs whenever the content looks masked.
        if not usage and not _PII_PLACEHOLDER_RE.search(assistant_content):
            return None

        assistant_message = {"role": "assistant", "content": assistant_content}
        if usage:
            assistant_message["usage"] = usage

        outlet_body = {
            "chat_id": chat_id,
            "model": payload["model"],
            "messages": payload["messages"] + [assistant_message],
            # `metadata` is guaranteed a dict here: we returned early above unless
            # chat_id was truthy, and chat_id only comes from a dict metadata.
            "metadata": metadata,
        }

        # Bound the outlet call so a slow/stuck pipeline can never hang or crash
        # the chat response (this runs on the awaited critical path). On timeout
        # or cancellation, fall back to the masked content.
        try:
            restored_body = await asyncio.wait_for(
                process_pipeline_outlet_filter(request, outlet_body, user, models),
                timeout=PII_TASK_OUTLET_RESTORE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log.warning(
                "PII task outlet restore timed out after %ss; "
                "falling back to masked content",
                PII_TASK_OUTLET_RESTORE_TIMEOUT,
            )
            return None
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so the outer `except Exception`
            # does NOT catch it — without this it propagates up through
            # background_tasks_handler and crashes the whole chat response. This
            # restore is a best-effort enhancement; swallow it and fall back to
            # the masked content so the chat survives.
            log.warning(
                "PII task outlet restore cancelled; falling back to masked content"
            )
            return None

        # The outlet returns the processed body; the restored assistant content is
        # the last message we appended. Extract it defensively.
        if isinstance(restored_body, dict):
            restored_messages = restored_body.get("messages") or []
            if restored_messages:
                restored_content = restored_messages[-1].get("content")
                if isinstance(restored_content, str):
                    return restored_content
        return None
    except Exception as exc:
        log.warning(f"_report_task_usage_and_restore failed: {exc}")
        return None


def _apply_restored_content(response, restored_content):
    """Overwrite the assistant content of an OpenAI-shaped response dict with the
    outlet-restored value.

    Returns the response unchanged when there is nothing to apply (restored_content
    is None — e.g. masking OFF, streaming, or outlet failure) or the response is not
    the expected shape, so callers can use it unconditionally:
        return _apply_restored_content(response, restored)
    """
    if restored_content is None or not isinstance(response, dict):
        return response
    try:
        response["choices"][0]["message"]["content"] = restored_content
    except (KeyError, IndexError, TypeError):
        pass
    return response


##################################
#
# Task Endpoints
#
##################################


class ActiveChatsForm(BaseModel):
    chat_ids: list[str]


@router.post('/active/chats')
async def check_active_chats(request: Request, form_data: ActiveChatsForm, user=Depends(get_verified_user)):
    """Check which chat IDs have active tasks."""
    from open_webui.tasks import get_active_chat_ids

    active = await get_active_chat_ids(request.app.state.redis, form_data.chat_ids)
    return {'active_chat_ids': active}


@router.get('/config')
async def get_task_config(request: Request, user=Depends(get_verified_user)):
    return {
        'TASK_MODEL': request.app.state.config.TASK_MODEL,
        'TASK_MODEL_EXTERNAL': request.app.state.config.TASK_MODEL_EXTERNAL,
        'TITLE_GENERATION_PROMPT_TEMPLATE': request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE,
        'IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE': request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_AUTOCOMPLETE_GENERATION': request.app.state.config.ENABLE_AUTOCOMPLETE_GENERATION,
        'AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH': request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH,
        'TAGS_GENERATION_PROMPT_TEMPLATE': request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE,
        'FOLLOW_UP_GENERATION_PROMPT_TEMPLATE': request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_FOLLOW_UP_GENERATION': request.app.state.config.ENABLE_FOLLOW_UP_GENERATION,
        'ENABLE_TAGS_GENERATION': request.app.state.config.ENABLE_TAGS_GENERATION,
        'ENABLE_TITLE_GENERATION': request.app.state.config.ENABLE_TITLE_GENERATION,
        'ENABLE_SEARCH_QUERY_GENERATION': request.app.state.config.ENABLE_SEARCH_QUERY_GENERATION,
        'ENABLE_RETRIEVAL_QUERY_GENERATION': request.app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION,
        'QUERY_GENERATION_PROMPT_TEMPLATE': request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE,
        'TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE': request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
        'VOICE_MODE_PROMPT_TEMPLATE': request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE,
    }


class TaskConfigForm(BaseModel):
    TASK_MODEL: Optional[str]
    TASK_MODEL_EXTERNAL: Optional[str]
    ENABLE_TITLE_GENERATION: bool
    TITLE_GENERATION_PROMPT_TEMPLATE: str
    IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE: str
    ENABLE_AUTOCOMPLETE_GENERATION: bool
    AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH: int
    TAGS_GENERATION_PROMPT_TEMPLATE: str
    FOLLOW_UP_GENERATION_PROMPT_TEMPLATE: str
    ENABLE_FOLLOW_UP_GENERATION: bool
    ENABLE_TAGS_GENERATION: bool
    ENABLE_SEARCH_QUERY_GENERATION: bool
    ENABLE_RETRIEVAL_QUERY_GENERATION: bool
    QUERY_GENERATION_PROMPT_TEMPLATE: str
    TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE: str
    VOICE_MODE_PROMPT_TEMPLATE: Optional[str]


@router.post('/config/update')
async def update_task_config(request: Request, form_data: TaskConfigForm, user=Depends(get_admin_user)):
    request.app.state.config.TASK_MODEL = form_data.TASK_MODEL
    request.app.state.config.TASK_MODEL_EXTERNAL = form_data.TASK_MODEL_EXTERNAL
    request.app.state.config.ENABLE_TITLE_GENERATION = form_data.ENABLE_TITLE_GENERATION
    request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE = form_data.TITLE_GENERATION_PROMPT_TEMPLATE

    request.app.state.config.ENABLE_FOLLOW_UP_GENERATION = form_data.ENABLE_FOLLOW_UP_GENERATION
    request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE = form_data.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE

    request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE = form_data.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE

    request.app.state.config.ENABLE_AUTOCOMPLETE_GENERATION = form_data.ENABLE_AUTOCOMPLETE_GENERATION
    request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH = (
        form_data.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH
    )

    request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE = form_data.TAGS_GENERATION_PROMPT_TEMPLATE
    request.app.state.config.ENABLE_TAGS_GENERATION = form_data.ENABLE_TAGS_GENERATION
    request.app.state.config.ENABLE_SEARCH_QUERY_GENERATION = form_data.ENABLE_SEARCH_QUERY_GENERATION
    request.app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION = form_data.ENABLE_RETRIEVAL_QUERY_GENERATION

    request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE = form_data.QUERY_GENERATION_PROMPT_TEMPLATE
    request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE = form_data.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE

    request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE = form_data.VOICE_MODE_PROMPT_TEMPLATE

    return {
        'TASK_MODEL': request.app.state.config.TASK_MODEL,
        'TASK_MODEL_EXTERNAL': request.app.state.config.TASK_MODEL_EXTERNAL,
        'ENABLE_TITLE_GENERATION': request.app.state.config.ENABLE_TITLE_GENERATION,
        'TITLE_GENERATION_PROMPT_TEMPLATE': request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE,
        'IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE': request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_AUTOCOMPLETE_GENERATION': request.app.state.config.ENABLE_AUTOCOMPLETE_GENERATION,
        'AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH': request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH,
        'TAGS_GENERATION_PROMPT_TEMPLATE': request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_TAGS_GENERATION': request.app.state.config.ENABLE_TAGS_GENERATION,
        'ENABLE_FOLLOW_UP_GENERATION': request.app.state.config.ENABLE_FOLLOW_UP_GENERATION,
        'FOLLOW_UP_GENERATION_PROMPT_TEMPLATE': request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE,
        'ENABLE_SEARCH_QUERY_GENERATION': request.app.state.config.ENABLE_SEARCH_QUERY_GENERATION,
        'ENABLE_RETRIEVAL_QUERY_GENERATION': request.app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION,
        'QUERY_GENERATION_PROMPT_TEMPLATE': request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE,
        'TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE': request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
        'VOICE_MODE_PROMPT_TEMPLATE': request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE,
    }


@router.post('/title/completions')
async def generate_title(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not request.app.state.config.ENABLE_TITLE_GENERATION:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Title generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model not found',
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating chat title using model {task_model_id} for user {user.email} ')

    if request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE != '':
        template = request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE

    content = title_generation_template(template, form_data['messages'], user)

    max_tokens = models[task_model_id].get('info', {}).get('params', {}).get('max_tokens', 1000)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        **(
            {'max_tokens': max_tokens}
            if models[task_model_id].get('owned_by') == 'ollama'
            else {
                'max_completion_tokens': max_tokens,
            }
        ),
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.TITLE_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        # PII restore: task inlet masking sent [PERSON_1] to the LLM, so `response`
        # is masked. Un-mask it via the outlet before returning so persisted title/
        # tags and follow-up chips show the real value (not [PERSON_1]).
        restored = await _report_task_usage_and_restore(
            request, user, payload, response, models
        )
        return _apply_restored_content(response, restored)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/follow_up/completions')
async def generate_follow_ups(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not request.app.state.config.ENABLE_FOLLOW_UP_GENERATION:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Follow-up generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model not found',
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating chat title using model {task_model_id} for user {user.email} ')

    if request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE != '':
        template = request.app.state.config.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_FOLLOW_UP_GENERATION_PROMPT_TEMPLATE

    content = follow_up_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.FOLLOW_UP_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        # PII restore: task inlet masking sent [PERSON_1] to the LLM, so `response`
        # is masked. Un-mask it via the outlet before returning so persisted title/
        # tags and follow-up chips show the real value (not [PERSON_1]).
        restored = await _report_task_usage_and_restore(
            request, user, payload, response, models
        )
        return _apply_restored_content(response, restored)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/tags/completions')
async def generate_chat_tags(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not request.app.state.config.ENABLE_TAGS_GENERATION:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'detail': 'Tags generation is disabled'},
        )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model not found',
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating chat tags using model {task_model_id} for user {user.email} ')

    if request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE != '':
        template = request.app.state.config.TAGS_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TAGS_GENERATION_PROMPT_TEMPLATE

    content = tags_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.TAGS_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        # PII restore: task inlet masking sent [PERSON_1] to the LLM, so `response`
        # is masked. Un-mask it via the outlet before returning so persisted title/
        # tags and follow-up chips show the real value (not [PERSON_1]).
        restored = await _report_task_usage_and_restore(
            request, user, payload, response, models
        )
        return _apply_restored_content(response, restored)
    except Exception as e:
        log.error(f'Error generating chat completion: {e}')
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/image_prompt/completions')
async def generate_image_prompt(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model not found',
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating image prompt using model {task_model_id} for user {user.email} ')

    if request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE != '':
        template = request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE

    content = image_prompt_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.IMAGE_PROMPT_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        # NO PII restore here (deliberate): the generated image prompt is fed to a
        # downstream image-generation backend that may be external. Restoring
        # [PERSON_1] -> real name would re-introduce PII into that external call and
        # defeat masking. Keep the masked prompt. See per-task restore decision.
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error('Exception occurred', exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/queries/completions')
async def generate_queries(request: Request, form_data: dict, user=Depends(get_verified_user)):
    type = form_data.get('type')
    if type == 'web_search':
        if not request.app.state.config.ENABLE_SEARCH_QUERY_GENERATION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Search query generation is disabled',
            )
    elif type == 'retrieval':
        if not request.app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Query generation is disabled',
            )

    if getattr(request.state, 'cached_queries', None):
        log.info(f'Reusing cached queries: {request.state.cached_queries}')
        return request.state.cached_queries

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model not found',
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating {type} queries using model {task_model_id} for user {user.email}')

    if (request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE).strip() != '':
        template = request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE

    content = query_generation_template(template, form_data['messages'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.QUERY_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        # NO PII restore here (deliberate): generated queries are fed to a downstream
        # search stage (web_search -> external engine; retrieval -> internal vector
        # DB), never shown to the user. Restoring [PERSON_1] -> real name would leak
        # PII to an external search engine. Keep the masked query. (Trade-off: masked
        # queries retrieve worse against real-name docs in internal RAG — a known,
        # out-of-scope cost of masking.) See per-task restore decision.
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )


@router.post('/auto/completions')
async def generate_autocompletion(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if not request.app.state.config.ENABLE_AUTOCOMPLETE_GENERATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Autocompletion generation is disabled',
        )

    type = form_data.get('type')
    prompt = form_data.get('prompt')
    messages = form_data.get('messages')

    if request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH > 0:
        if len(prompt) > request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Input prompt exceeds maximum length of {request.app.state.config.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH}',
            )

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model not found',
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating autocompletion using model {task_model_id} for user {user.email}')

    if (request.app.state.config.AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE).strip() != '':
        template = request.app.state.config.AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE

    content = autocomplete_generation_template(template, prompt, messages, type, user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.AUTOCOMPLETE_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        # PII restore: the autocompletion suggestion is shown to the user (typed into
        # their input box), so un-mask [PERSON_1] before returning.
        restored = await _report_task_usage_and_restore(
            request, user, payload, response, models
        )
        return _apply_restored_content(response, restored)
    except Exception as e:
        log.error(f'Error generating chat completion: {e}')
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={'detail': 'An internal error has occurred.'},
        )


@router.post('/emoji/completions')
async def generate_emoji(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model not found',
        )

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(f'generating emoji using model {task_model_id} for user {user.email} ')

    template = DEFAULT_EMOJI_GENERATION_PROMPT_TEMPLATE

    content = emoji_generation_template(template, form_data['prompt'], user)

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': False,
        **(
            {'max_tokens': 4}
            if models[task_model_id].get('owned_by') == 'ollama'
            else {
                'max_completion_tokens': 4,
            }
        ),
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': str(TASKS.EMOJI_GENERATION),
            'task_body': form_data,
            'chat_id': form_data.get('chat_id', None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        # PII restore: the emoji is shown to the user. Restore for consistency —
        # in practice a 4-token emoji almost never contains a placeholder, so this
        # is effectively a no-op, but it keeps the user-visible path uniform.
        restored = await _report_task_usage_and_restore(
            request, user, payload, response, models
        )
        return _apply_restored_content(response, restored)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )


@router.post('/moa/completions')
async def generate_moa_response(request: Request, form_data: dict, user=Depends(get_verified_user)):
    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']

    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model not found',
        )

    template = DEFAULT_MOA_GENERATION_PROMPT_TEMPLATE

    content = moa_response_generation_template(
        template,
        form_data['prompt'],
        form_data['responses'],
    )

    payload = {
        'model': model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': form_data.get('stream', False),
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'chat_id': form_data.get('chat_id', None),
            'task': str(TASKS.MOA_RESPONSE_GENERATION),
            'task_body': form_data,
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        # PII restore: the MoA response is the final merged answer shown to the user.
        # For streaming (stream=True) the response is not dict-shaped; the helper
        # returns None and the streamed body is passed through unchanged.
        restored = await _report_task_usage_and_restore(
            request, user, payload, response, models
        )
        return _apply_restored_content(response, restored)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={'detail': str(e)},
        )
