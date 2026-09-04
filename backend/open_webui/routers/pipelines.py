import asyncio
import logging
import os
import shutil
from typing import Optional

import aiohttp
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from open_webui.config import CACHE_DIR, PII_MASKING_ENFORCED_PERMISSION
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT_SOCK_READ,
    PII_FILTER_IDS,
)
from open_webui.routers.openai import get_all_models_responses
from open_webui.utils.auth import get_admin_user
from open_webui.utils.access_control import has_permission
from open_webui.utils.pii_chunking import (
    PII_INLET_CHUNK_CHARS,
    PII_INLET_CHUNK_RETRIES,
    PII_INLET_CONCURRENCY,
    PII_INLET_TOTAL_BUDGET_S,
    estimated_masking_seconds,
    split_text_for_pii,
)
from pydantic import BaseModel
from starlette.responses import FileResponse

log = logging.getLogger(__name__)


##################################
#
# Team PII masking policy — mandatory masking
#
##################################

# The permission key (PII_MASKING_ENFORCED_PERMISSION) is defined in config.py,
# beside its default. Named as a RESTRICTION on purpose: True means "masking is
# mandatory for this user", never "user may switch it off". Multi-group
# permissions merge with `permissions[key] or value`, so a restriction yields
# "strictest wins" while a freedom would yield "loosest wins" — the same line of
# code, the opposite security outcome.

# Attribute holding the per-request memo. Keyed by user id rather than stored
# as a bare bool so a single request can never hand one user another user's
# resolved policy.
_PII_POLICY_MEMO_ATTR = '_pii_masking_enforced_memo'


async def resolve_pii_masking_enforced(request, user) -> bool:
    """Whether team policy makes PII masking mandatory for this user.

    Read-only overlay: this NEVER writes to user.settings. The user's own
    stored preference stays untouched underneath the policy and returns by
    itself when the policy is switched off.

    FAIL-CLOSED: any failure to determine the policy — unreachable DB,
    malformed `group.permissions`, anything — is treated as ENFORCED. Same
    rule that already governs PiiMaskingUnavailableError: when masking cannot
    be reasoned about, PII does not get to leave unmasked.

    Memoized per request. The eight task-generator endpoints are each their own
    HTTP request, so this resolves to one lookup per request rather than one
    per inlet call.

    NOTE: there is deliberately NO `user.role == 'admin'` bypass here,
    unlike the sibling `temporary_enforced` permission. That one governs UX;
    this one governs whether unmasked PII leaves the infrastructure. An admin
    who needs the policy lifted lifts it on the group — visibly, and with an
    audit trail — instead of bypassing it silently.
    """
    user_id = getattr(user, 'id', None)
    state = getattr(request, 'state', None)

    memo = getattr(state, _PII_POLICY_MEMO_ATTR, None) if state is not None else None
    if isinstance(memo, dict) and user_id in memo:
        # `in`, not truthiness — a memoized False must not be recomputed.
        return memo[user_id]

    try:
        enforced = await has_permission(
            user_id,
            PII_MASKING_ENFORCED_PERMISSION,
            request.app.state.config.USER_PERMISSIONS,
        )
    except Exception as e:
        # Do not swallow this quietly: a DB outage would otherwise surface as
        # every user's toggle mysteriously locking, with nothing in the log.
        log.warning(
            f'[pii_policy] could not resolve masking policy for user_id={user_id}; failing closed (enforced=True): {e}'
        )
        enforced = True

    if state is not None:
        if not isinstance(memo, dict):
            memo = {}
            setattr(state, _PII_POLICY_MEMO_ATTR, memo)
        memo[user_id] = enforced

    return enforced


##################################
#
# Pipeline Middleware
# Every hand this passes through can corrupt it or
# improve it. Let each stage leave it better than it found.
#
##################################


class PiiMaskingUnavailableError(Exception):
    """Raised when PII masking is required for a request but the masking pipeline
    could not be applied — unreachable (connection error) or not present in the
    resolved model registry. FAIL-CLOSED: the request must be refused so unmasked
    PII never reaches the LLM. Its str() is shown to the user verbatim (surfaced
    via chat:message:error), so keep the message clear and non-technical.
    """

    DEFAULT_MESSAGE = (
        'PII masking is currently unavailable, so your message was not sent. Please try again in a moment.'
    )

    def __init__(self, message: Optional[str] = None):
        super().__init__(message or self.DEFAULT_MESSAGE)


def resolve_request_pii_masking(payload) -> Optional[bool]:
    """Effective per-request PII masking flag, read from the payload's `features`
    (top-level, else `metadata.features` — the TRAU-522 Layer-1 coupling). Returns
    True/False when explicitly set, or None when unspecified. This is the SAME
    signal the inlet override (below) uses, so the fail-closed guard and the
    pipeline agree on whether masking was requested.
    """
    features = payload.get('features')
    if not isinstance(features, dict):
        metadata = payload.get('metadata')
        features = metadata.get('features') if isinstance(metadata, dict) else None
    value = features.get('pii_masking') if isinstance(features, dict) else None
    return value if isinstance(value, bool) else None


def assert_pii_masking_available(payload, model_id, models, policy_enforced=False) -> None:
    """FAIL-CLOSED guard for Mechanism 2 (registry pruning).

    When PII masking is required for this request but NO PII filter is present in
    the resolved filters — e.g. the pipeline is down and its filter was pruned
    from `app.state.MODELS` because its `/models` fetch returned None — the inlet
    loop would silently skip masking and PII would reach the LLM. Refuse the
    request instead.

    "Required" means REQUESTED **or** MANDATED. `policy_enforced` carries the team
    policy. Reading only the payload here would leave a silent hole: under an
    enforcing policy a user who switches the in-chat toggle off sends
    features.pii_masking=False, this guard would no-op, and if the pipeline is
    down the loop below has nothing to iterate — so the message would go out
    unmasked with no error at all.

    No-ops (does NOT block) when:
      * enforcement is disabled (`PII_FILTER_IDS` empty), or
      * masking is neither requested nor mandated — so a chat with masking OFF and
        no policy is never blocked by pipeline unavailability, or
      * a PII filter IS present in the resolved filters (normal path; if the call
        then fails, the inlet's own fail-closed re-raise handles it).
    """
    if not PII_FILTER_IDS:
        return
    if not policy_enforced and resolve_request_pii_masking(payload) is not True:
        return
    resolved_ids = {f.get('id') for f in get_sorted_filters(model_id, models)}
    if not (resolved_ids & PII_FILTER_IDS):
        raise PiiMaskingUnavailableError()


def get_sorted_filters(model_id, models):
    filters = [
        model
        for model in models.values()
        if 'pipeline' in model
        and 'type' in model['pipeline']
        and model['pipeline']['type'] == 'filter'
        and (
            model['pipeline']['pipelines'] == ['*']
            or any(model_id == target_model_id for target_model_id in model['pipeline']['pipelines'])
        )
    ]
    sorted_filters = sorted(filters, key=lambda x: x['pipeline']['priority'])
    return sorted_filters


async def _post_inlet_once(session, url, key, filter_id, request_data):
    """One inlet POST. Extracted from `process_pipeline_inlet_filter` so the
    chunked path (TRAU-543) can issue the same call many times. Behaviour is
    unchanged: `ClientResponseError` is translated into an HTTPException that
    preserves the pipeline's own `detail`, everything else propagates so the
    caller's fail-closed branch decides."""
    async with session.post(
        f'{url}/{filter_id}/filter/inlet',
        headers={'Authorization': f'Bearer {key}'},
        json=request_data,
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
    ) as response:
        try:
            response.raise_for_status()
            return await response.json()
        except aiohttp.ClientResponseError as e:
            try:
                res = await response.json() if 'application/json' in response.content_type else {}
                if 'detail' in res:
                    raise HTTPException(status_code=response.status, detail=res['detail'])
            except HTTPException:
                raise
            except Exception:
                pass
            raise HTTPException(status_code=response.status, detail=e.message)


def _oversized_message_indices(payload):
    """Indices of messages whose content alone would blow the per-call budget."""
    return [
        i
        for i, m in enumerate(payload.get('messages') or [])
        if isinstance(m.get('content'), str) and len(m['content']) > PII_INLET_CHUNK_CHARS
    ]


def _payload_chat_id(payload):
    """The payload's chat/conversation id, checked at both places it can
    appear: a top-level `chat_id`, and the far more common `metadata.chat_id`
    (see `routers/tasks.py`'s outlet-restore helper for the latter). Returns
    None unless one of them is a non-empty string.
    """
    metadata = payload.get('metadata')
    for candidate in (
        payload.get('chat_id'),
        metadata.get('chat_id') if isinstance(metadata, dict) else None,
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


async def _mask_oversized_via_chunks(session, url, key, filter_id, payload, user_with_valves, on_progress):
    """Mask a payload whose message(s) exceed the per-call budget.

    One skeleton call carries the conversation with the oversized contents
    BLANKED (not removed — see Key Decision 5: deleting a message shifts the
    pipeline's last-user / last-assistant indices and silently changes which
    history entries get NER). Every oversized content is then masked as
    independent sub-chunks, concurrently, and spliced back in document order.

    Concurrency is safe: `ThreadVault.get_placeholder` is atomic get-or-mint and
    idempotent under concurrency, so racing chunks that contain the same value
    receive the same placeholder. Only the assigned number may differ from
    sequential order, which nothing depends on.

    FAIL-CLOSED: any chunk still failing after `PII_INLET_CHUNK_RETRIES`, and any
    overrun of `PII_INLET_TOTAL_BUDGET_S`, raises `PiiMaskingUnavailableError`.

    Refuses outright, before issuing any POST, when `estimated_masking_seconds`
    for this payload exceeds `PII_INLET_TOTAL_BUDGET_S`. The estimate is split
    in two: the skeleton POST carries every non-oversized message at full
    length and runs once, sequentially (charged at 1x, no speedup applies to
    it), while only the oversized messages' chunk POSTs benefit from
    concurrency (charged at the measured speedup). Summing everything and
    applying the speedup to the whole payload would let a history-heavy chat
    with only a modestly oversized paste slip past the guard and straight into
    the `PII_INLET_TOTAL_BUDGET_S` timeout below — paying the wall-clock cost
    first and refusing anyway. Refusing up front is the honest answer.

    Requires a stable chat_id (see `_payload_chat_id`). Every chunk POST
    inherits `payload['metadata']`, and the pipeline's own chat-id resolver
    mints a FRESH ephemeral thread per call whenever chat_id is missing —
    each chunk would then land in its own PII vault, placeholder numbering
    would restart per chunk, distinct people would collapse onto a shared
    `[PERSON_1]` across the reassembled prompt, and the outlet could never
    resolve them back. Refuse rather than mint a shared synthetic id: that
    would write vault rows no chat deletion could ever reclaim. The
    non-chunked path is unaffected — it is not exposed to this failure mode
    since a single call inherits the real chat_id like it always has.
    """
    messages = payload.get('messages') or []
    indices = _oversized_message_indices(payload)
    oversized = set(indices)
    chunked_chars = sum(
        len(m['content']) for i, m in enumerate(messages) if i in oversized and isinstance(m.get('content'), str)
    )
    skeleton_chars = sum(
        len(m['content']) for i, m in enumerate(messages) if i not in oversized and isinstance(m.get('content'), str)
    )
    if estimated_masking_seconds(skeleton_chars, chunked_chars) > PII_INLET_TOTAL_BUDGET_S:
        raise PiiMaskingUnavailableError(
            'This message is too long to mask safely. Shorten it, or attach the text as a file instead.'
        )

    if _payload_chat_id(payload) is None:
        raise PiiMaskingUnavailableError(
            'PII masking is currently unavailable for a message this large without an active '
            'conversation. Please try again from an existing chat.'
        )

    skeleton = {
        **payload,
        'messages': [{**m, 'content': ''} if i in oversized else m for i, m in enumerate(messages)],
    }

    jobs = []  # (message_index, piece_index, text)
    for i in indices:
        for piece_index, (_offset, piece) in enumerate(split_text_for_pii(messages[i]['content'])):
            jobs.append((i, piece_index, piece))

    total = len(jobs)
    done = 0
    semaphore = asyncio.Semaphore(PII_INLET_CONCURRENCY)
    results: dict[tuple[int, int], str] = {}

    async def _mask_piece(msg_index, piece_index, piece):
        nonlocal done
        body = {
            **payload,
            'messages': [{**messages[msg_index], 'content': piece}],
        }
        last_exc = None
        for attempt in range(PII_INLET_CHUNK_RETRIES):
            try:
                async with semaphore:
                    out = await _post_inlet_once(
                        session,
                        url,
                        key,
                        filter_id,
                        {'user': user_with_valves, 'body': body},
                    )
                results[(msg_index, piece_index)] = out['messages'][0]['content']
                done += 1
                if on_progress is not None:
                    on_progress(done, total)
                return
            except HTTPException:
                raise  # the pipeline said no on purpose; do not retry
            except Exception as e:
                last_exc = e
                if attempt + 1 < PII_INLET_CHUNK_RETRIES:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise PiiMaskingUnavailableError() from last_exc

    async def _run_all():
        skeleton_out = await _post_inlet_once(
            session,
            url,
            key,
            filter_id,
            {'user': user_with_valves, 'body': skeleton},
        )
        await asyncio.gather(*(_mask_piece(*job) for job in jobs))
        return skeleton_out

    try:
        out = await asyncio.wait_for(_run_all(), timeout=PII_INLET_TOTAL_BUDGET_S)
    except asyncio.TimeoutError as e:
        log.warning(
            '[pii_chunking] masking exceeded %ss for %d chunks; refusing the request',
            PII_INLET_TOTAL_BUDGET_S,
            total,
        )
        raise PiiMaskingUnavailableError() from e

    # FAIL-CLOSED (finding #1): a missing/empty `messages` in the skeleton
    # response must never fall back to the caller's ORIGINAL, unmasked
    # content, and a different-length response would either misalign a
    # masked chunk onto the wrong history entry (via `out_messages[i]`) or
    # raise a confusing IndexError. Refuse instead of guessing.
    out_messages = out.get('messages')
    if not out_messages or len(out_messages) != len(messages):
        raise PiiMaskingUnavailableError()
    out_messages = list(out_messages)
    for i in indices:
        # Finding #2: the expected piece count comes from the known `jobs`,
        # not from however many keys happen to be in `results` — deriving it
        # from `results` would silently TRUNCATE the message to its first k
        # pieces if a future change tolerates partial `gather` failures
        # (e.g. `return_exceptions=True`). `results[(i, p)]` raises KeyError
        # for a missing piece, which the call site's generic exception
        # handler turns into `PiiMaskingUnavailableError` for PII filters.
        pieces = [results[(i, p)] for p in range(sum(1 for j in jobs if j[0] == i))]
        out_messages[i] = {**out_messages[i], 'content': ''.join(pieces)}
    return {**out, 'messages': out_messages}


async def process_pipeline_inlet_filter(request, payload, user, models, *, on_progress=None):
    # Extract user.settings as a plain dict. user.settings is a UserSettings
    # Pydantic instance (models/users.py:40-43) with extra="allow", so arbitrary
    # keys like "pipelines" survive model_dump(). The isinstance(dict) branch is
    # defense-in-depth for unusual ORM states (e.g. raw dict from legacy data).
    if user.settings is None:
        user_settings_dict: dict = {}
    elif isinstance(user.settings, dict):
        user_settings_dict = user.settings
    else:
        user_settings_dict = user.settings.model_dump()

    # Settings are stored under the "ui" key (frontend sends
    # `updateUserSettings(..., { ui: ... })`; see SettingsModal.svelte:554-559).
    # The stored shape is:
    #   user.settings.ui.pipelines.valves.<filter_id>.<key>
    # (not user.settings.pipelines.* as the spec originally assumed).
    ui_settings = user_settings_dict.get('ui', {})
    if not isinstance(ui_settings, dict):
        ui_settings = {}

    pipelines_settings = ui_settings.get('pipelines', {})
    if not isinstance(pipelines_settings, dict):
        pipelines_settings = {}

    all_filter_valves = pipelines_settings.get('valves', {})
    if not isinstance(all_filter_valves, dict):
        all_filter_valves = {}

    # user dict from openai.py is intentionally rebuilt here to inject
    # per-user, per-filter valves. See TASK-3.7a-SPEC.md §3.2.
    base_user_dict = {
        'id': user.id,
        'email': user.email,
        'name': user.name,
        'role': user.role,
    }
    model_id = payload['model']
    sorted_filters = get_sorted_filters(model_id, models)

    # Team policy, resolved ONCE per inlet call and memoized per request, so the
    # eight task-generator endpoints cost one lookup each rather than one per
    # filter. Fail-closed: if the policy cannot be determined at all,
    # the resolver returns True. Read-only — never written back to user.settings.
    policy_enforced = await resolve_pii_masking_enforced(request, user)

    # FAIL-CLOSED guard (Mechanism 2), enforced HERE — the single chokepoint every
    # inlet caller flows through (main chat AND all task generators). If PII
    # masking is required for this request but no PII filter is present in the
    # resolved filters (e.g. the pipeline is down and its filter was pruned from
    # app.state.MODELS because its /models fetch returned None), the loop below
    # would silently skip masking and PII would reach the LLM. Refuse first.
    # `policy_enforced` is passed so "required" covers MANDATED, not just
    # requested — without it an enforced user who toggled masking off in chat
    # would slip through this guard entirely.
    # No-op when masking is neither requested nor mandated, or no PII filter is
    # configured.
    assert_pii_masking_available(payload, model_id, models, policy_enforced)

    model = models[model_id]

    if 'pipeline' in model:
        sorted_filters.append(model)

    async with aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(sock_read=AIOHTTP_CLIENT_TIMEOUT_SOCK_READ),
    ) as session:
        for filter in sorted_filters:
            urlIdx = filter.get('urlIdx')

            try:
                urlIdx = int(urlIdx)
            except Exception:
                continue

            url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
            key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

            if not key:
                continue

            # Per-filter valves injection (TASK-3.7a). Each filter gets its own
            # valves dict from user.settings["ui"]["pipelines"]["valves"][filter_id].
            filter_id = filter.get('id')
            per_filter_valves = all_filter_valves.get(filter_id, {})
            if not isinstance(per_filter_valves, dict):
                per_filter_valves = {}

            # Per-request override from features.pii_masking (top-level, else
            # metadata.features — see resolve_request_pii_masking) takes precedence
            # over the stored user setting, letting the per-chat toggle control
            # masking without a DB write. Top-level wins when both are present.
            request_pii = resolve_request_pii_masking(payload)
            if isinstance(request_pii, bool):
                per_filter_valves = {**per_filter_valves, 'pii_masking_enabled': request_pii}

            # Team policy wins over both the stored valve and the per-request
            # override, so it is applied LAST. Reversing these two blocks hands
            # the user's False the final word over the policy — the same lines of
            # code, the opposite outcome.
            if policy_enforced and filter_id in PII_FILTER_IDS:
                per_filter_valves = {**per_filter_valves, 'pii_masking_enabled': True}

            log.debug(
                f'[pii_toggle] filter_id={filter_id} pii_masking_enabled={per_filter_valves.get("pii_masking_enabled")}'
            )
            user_with_valves = {**base_user_dict, 'valves': per_filter_valves}

            request_data = {
                'user': user_with_valves,
                'body': payload,
            }

            try:
                if (
                    filter_id in PII_FILTER_IDS
                    and per_filter_valves.get('pii_masking_enabled', True)
                    and _oversized_message_indices(payload)
                ):
                    # TRAU-543: one call cannot carry this much text. The inlet
                    # runs at a flat ~240 chars/s, so a 150k paste is ~625 s
                    # serially and would blow the 60 s socket read long before
                    # that. Split it and run the pieces concurrently.
                    payload = await _mask_oversized_via_chunks(
                        session,
                        url,
                        key,
                        filter_id,
                        payload,
                        user_with_valves,
                        on_progress,
                    )
                else:
                    payload = await _post_inlet_once(session, url, key, filter['id'], request_data)
            except PiiMaskingUnavailableError:
                raise
            except HTTPException:
                raise
            except Exception as e:
                log.exception(f'Connection error: {e}')
                # FAIL-CLOSED for PII filters (Mechanism 1): a connection error
                # here means masking did NOT run. For a required PII filter with
                # masking enabled, the original (unmasked) `payload` must NOT be
                # returned to the caller — refuse the request instead. Every other
                # filter keeps best-effort passthrough (e.g. a telemetry outage
                # must never block chat). `pii_masking_enabled` defaults to True
                # (the pipeline default) when neither the per-request override nor
                # a stored valve set it.
                if filter_id in PII_FILTER_IDS and per_filter_valves.get('pii_masking_enabled', True):
                    raise PiiMaskingUnavailableError() from e

    return payload


async def process_pipeline_outlet_filter(request, payload, user, models):
    user = {'id': user.id, 'email': user.email, 'name': user.name, 'role': user.role}
    model_id = payload['model']
    sorted_filters = get_sorted_filters(model_id, models)
    model = models[model_id]

    if 'pipeline' in model:
        sorted_filters = [model] + sorted_filters

    async with aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(sock_read=AIOHTTP_CLIENT_TIMEOUT_SOCK_READ),
    ) as session:
        for filter in sorted_filters:
            urlIdx = filter.get('urlIdx')

            try:
                urlIdx = int(urlIdx)
            except Exception:
                continue

            url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
            key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

            if not key:
                continue

            headers = {'Authorization': f'Bearer {key}'}
            request_data = {
                'user': user,
                'body': payload,
            }

            try:
                async with session.post(
                    f'{url}/{filter["id"]}/filter/outlet',
                    headers=headers,
                    json=request_data,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
            except aiohttp.ClientResponseError as e:
                try:
                    res = await response.json() if 'application/json' in response.content_type else {}
                    if 'detail' in res:
                        raise HTTPException(
                            status_code=response.status,
                            detail=res['detail'],
                        )
                except HTTPException:
                    raise
                except Exception:
                    pass

                raise HTTPException(
                    status_code=response.status,
                    detail=e.message,
                )
            except HTTPException:
                raise
            except Exception as e:
                log.exception(f'Connection error: {e}')

    return payload


##################################
#
# Pipelines Endpoints
#
##################################

router = APIRouter()


@router.get('/list')
async def get_pipelines_list(request: Request, user=Depends(get_admin_user)):
    responses = await get_all_models_responses(request, user)
    log.debug(f'get_pipelines_list: get_openai_models_responses returned {responses}')

    urlIdxs = [idx for idx, response in enumerate(responses) if response is not None and 'pipelines' in response]

    return {
        'data': [
            {
                'url': request.app.state.config.OPENAI_API_BASE_URLS[urlIdx],
                'idx': urlIdx,
            }
            for urlIdx in urlIdxs
        ]
    }


@router.post('/upload')
async def upload_pipeline(
    request: Request,
    urlIdx: int = Form(...),
    file: UploadFile = File(...),
    user=Depends(get_admin_user),
):
    log.info(f'upload_pipeline: urlIdx={urlIdx}, filename={file.filename}')
    filename = os.path.basename(file.filename)

    # Check if the uploaded file is a python file
    if not (filename and filename.endswith('.py')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Only Python (.py) files are allowed.',
        )

    upload_folder = f'{CACHE_DIR}/pipelines'
    os.makedirs(upload_folder, exist_ok=True)
    file_path = os.path.join(upload_folder, filename)

    response = None
    try:
        # Save the uploaded file
        with open(file_path, 'wb') as buffer:
            shutil.copyfileobj(file.file, buffer)

        url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
        key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

        headers = {'Authorization': f'Bearer {key}'}

        async with aiohttp.ClientSession(trust_env=True) as session:
            with open(file_path, 'rb') as f:
                form_data = aiohttp.FormData()
                form_data.add_field(
                    'file',
                    f,
                    filename=filename,
                    content_type='application/octet-stream',
                )

                async with session.post(
                    f'{url}/pipelines/upload',
                    headers=headers,
                    data=form_data,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as response:
                    response.raise_for_status()
                    data = await response.json()

        return {**data}
    except Exception as e:
        # Handle connection error here
        log.exception(f'Connection error: {e}')

        detail = None
        status_code = status.HTTP_404_NOT_FOUND
        if response is not None:
            status_code = response.status
            try:
                res = await response.json()
                if 'detail' in res:
                    detail = res['detail']
            except Exception:
                pass

        raise HTTPException(
            status_code=status_code,
            detail=detail if detail else 'Pipeline not found',
        )
    finally:
        # Ensure the file is deleted after the upload is completed or on failure
        if os.path.exists(file_path):
            os.remove(file_path)


class AddPipelineForm(BaseModel):
    url: str
    urlIdx: int


@router.post('/add')
async def add_pipeline(request: Request, form_data: AddPipelineForm, user=Depends(get_admin_user)):
    response = None
    try:
        urlIdx = form_data.urlIdx

        url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
        key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(
                f'{url}/pipelines/add',
                headers={'Authorization': f'Bearer {key}'},
                json={'url': form_data.url},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        return {**data}
    except Exception as e:
        # Handle connection error here
        log.exception(f'Connection error: {e}')

        detail = None
        if response is not None:
            try:
                res = await response.json()
                if 'detail' in res:
                    detail = res['detail']
            except Exception:
                pass

        raise HTTPException(
            status_code=(response.status if response is not None else status.HTTP_404_NOT_FOUND),
            detail=detail if detail else 'Pipeline not found',
        )


class DeletePipelineForm(BaseModel):
    id: str
    urlIdx: int


@router.delete('/delete')
async def delete_pipeline(request: Request, form_data: DeletePipelineForm, user=Depends(get_admin_user)):
    response = None
    try:
        urlIdx = form_data.urlIdx

        url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
        key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.delete(
                f'{url}/pipelines/delete',
                headers={'Authorization': f'Bearer {key}'},
                json={'id': form_data.id},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        return {**data}
    except Exception as e:
        # Handle connection error here
        log.exception(f'Connection error: {e}')

        detail = None
        if response is not None:
            try:
                res = await response.json()
                if 'detail' in res:
                    detail = res['detail']
            except Exception:
                pass

        raise HTTPException(
            status_code=(response.status if response is not None else status.HTTP_404_NOT_FOUND),
            detail=detail if detail else 'Pipeline not found',
        )


@router.get('/')
async def get_pipelines(request: Request, urlIdx: Optional[int] = None, user=Depends(get_admin_user)):
    response = None
    try:
        url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
        key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                f'{url}/pipelines',
                headers={'Authorization': f'Bearer {key}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        return {**data}
    except Exception as e:
        # Handle connection error here
        log.exception(f'Connection error: {e}')

        detail = None
        if response is not None:
            try:
                res = await response.json()
                if 'detail' in res:
                    detail = res['detail']
            except Exception:
                pass

        raise HTTPException(
            status_code=(response.status if response is not None else status.HTTP_404_NOT_FOUND),
            detail=detail if detail else 'Pipeline not found',
        )


@router.get('/{pipeline_id}/valves')
async def get_pipeline_valves(
    request: Request,
    urlIdx: Optional[int],
    pipeline_id: str,
    user=Depends(get_admin_user),
):
    response = None
    try:
        url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
        key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                f'{url}/{pipeline_id}/valves',
                headers={'Authorization': f'Bearer {key}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        return {**data}
    except Exception as e:
        # Handle connection error here
        log.exception(f'Connection error: {e}')

        detail = None
        if response is not None:
            try:
                res = await response.json()
                if 'detail' in res:
                    detail = res['detail']
            except Exception:
                pass

        raise HTTPException(
            status_code=(response.status if response is not None else status.HTTP_404_NOT_FOUND),
            detail=detail if detail else 'Pipeline not found',
        )


@router.get('/{pipeline_id}/valves/spec')
async def get_pipeline_valves_spec(
    request: Request,
    urlIdx: Optional[int],
    pipeline_id: str,
    user=Depends(get_admin_user),
):
    response = None
    try:
        url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
        key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                f'{url}/{pipeline_id}/valves/spec',
                headers={'Authorization': f'Bearer {key}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        return {**data}
    except Exception as e:
        # Handle connection error here
        log.exception(f'Connection error: {e}')

        detail = None
        if response is not None:
            try:
                res = await response.json()
                if 'detail' in res:
                    detail = res['detail']
            except Exception:
                pass

        raise HTTPException(
            status_code=(response.status if response is not None else status.HTTP_404_NOT_FOUND),
            detail=detail if detail else 'Pipeline not found',
        )


@router.post('/{pipeline_id}/valves/update')
async def update_pipeline_valves(
    request: Request,
    urlIdx: Optional[int],
    pipeline_id: str,
    form_data: dict,
    user=Depends(get_admin_user),
):
    response = None
    try:
        url = request.app.state.config.OPENAI_API_BASE_URLS[urlIdx]
        key = request.app.state.config.OPENAI_API_KEYS[urlIdx]

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(
                f'{url}/{pipeline_id}/valves/update',
                headers={'Authorization': f'Bearer {key}'},
                json={**form_data},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        return {**data}
    except Exception as e:
        # Handle connection error here
        log.exception(f'Connection error: {e}')

        detail = None

        if response is not None:
            try:
                res = await response.json()
                if 'detail' in res:
                    detail = res['detail']
            except Exception:
                pass

        raise HTTPException(
            status_code=(response.status if response is not None else status.HTTP_404_NOT_FOUND),
            detail=detail if detail else 'Pipeline not found',
        )
