import asyncio
import logging
import os
import time
from typing import Optional

import aiofiles
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
    AIOHTTP_FILE_STREAM_CHUNK_SIZE,
    LLM_RETRY_RETRYABLE_STATUS,
    PII_FILTER_IDS,
)
from open_webui.events import EVENTS, publish_event
from open_webui.routers.openai import get_all_models_responses
from open_webui.utils.auth import get_admin_user
from open_webui.utils.access_control import has_permission
from open_webui.utils.pii_chunking import (
    PII_INLET_CHARS_PER_SECOND,
    PII_INLET_CHUNK_CHARS,
    PII_INLET_CHUNK_RETRIES,
    PII_INLET_CONCURRENCY,
    PII_INLET_RETRY_BACKOFF_S,
    PII_INLET_SKELETON_SAFETY_MARGIN,
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
    (top-level, else `metadata.features`). Returns True/False when explicitly
    set, or None when unspecified. The inlet override below reads the same
    signal, so the fail-closed guard and the pipeline agree on whether masking
    was requested.
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
    """Send one inlet POST to the pipeline filter and return its JSON body.

    An HTTP error status is raised as `HTTPException`, using the pipeline's own
    `detail` when the response has one. Any other exception (connection error,
    timeout) propagates unchanged so the caller decides whether to fail closed."""
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



async def _post_inlet_with_retry(
    session, url, key, filter_id, request_data, *, semaphore=None, stats=None, fail_closed=True
):
    """Send one inlet POST, retrying transient failures.

    `_post_inlet_once` raises `HTTPException` for two different kinds of
    failure, and they are handled differently:

      * A status in `LLM_RETRY_RETRYABLE_STATUS` (by default 429, transient 5xx
        and Cloudflare 52x) means the platform has no free instance yet. The
        request is retried with exponential backoff starting at
        `PII_INLET_RETRY_BACKOFF_S`.
      * Any other status (for example 400, 401, 403, 422) is a deliberate
        refusal by the pipeline. Retrying would return the same answer, so it
        is re-raised immediately and reaches the user with its own status.

    Connection errors and timeouts are retried on a short linear backoff
    (0.5 s, 1 s, 1.5 s): a dropped connection usually recovers quickly, while
    starting a new instance takes several seconds. The retryable statuses come
    from the shared `LLM_RETRY_RETRYABLE_STATUS` setting, so operators can
    override them with that env var.

    `semaphore` is held only around the POST, not during a backoff, so a
    waiting retry does not block other chunks.
    """
    last_exc = None
    for attempt in range(PII_INLET_CHUNK_RETRIES):
        try:
            if semaphore is None:
                return await _post_inlet_once(session, url, key, filter_id, request_data)
            async with semaphore:
                return await _post_inlet_once(session, url, key, filter_id, request_data)
        except HTTPException as e:
            if e.status_code not in LLM_RETRY_RETRYABLE_STATUS:
                raise  # the pipeline said no on purpose
            last_exc = e
            delay = PII_INLET_RETRY_BACKOFF_S * (2**attempt)
            # Logged at warning level so slow masking caused by instance
            # start-up can be told apart from slowness elsewhere.
            if stats is not None:
                stats['retries'] = stats.get('retries', 0) + 1
            log.warning(
                '[pii_chunking] inlet returned %s (attempt %d/%d); retrying in %.1fs',
                e.status_code,
                attempt + 1,
                PII_INLET_CHUNK_RETRIES,
                delay,
            )
        except Exception as e:  # noqa: BLE001 — network/timeout; fail closed below
            last_exc = e
            delay = 0.5 * (attempt + 1)
        if attempt + 1 < PII_INLET_CHUNK_RETRIES:
            await asyncio.sleep(delay)
    # All attempts failed. The caller chooses the outcome with `fail_closed`.
    # The chunked masking path uses True: a chunk that was never masked means
    # the message cannot be sent, so it raises `PiiMaskingUnavailableError`.
    # The single-call path uses False because it serves every filter, including
    # non-PII ones that must pass through on a connection error (a telemetry
    # outage must not block chat). It gets the original exception back, and
    # `process_pipeline_inlet_filter` decides per filter whether to fail closed.
    if fail_closed:
        raise PiiMaskingUnavailableError() from last_exc
    raise last_exc

# Background-task types (from `metadata.task`) that are never chunked. For these
# tasks the PII pipeline skips NER and only re-masks known values from the vault
# with a regex, which is fast at any payload size. Chunking exists only because
# NER is slow (about 240 characters per second), so these tasks always use a
# single call. Chunking them would apply the size guard and refuse large
# title/tags/follow-up requests that a single call handles. Keep this set in
# sync with `_LLM_FACING_SKIP_NER_TASKS` in the pipeline's
# `pii_filter_pipeline.py`.
#
# `query_generation` and `image_prompt_generation` are not exempt: their output
# goes to an external service (RAG search, image backend), so the pipeline runs
# full NER on them and large payloads need chunking. Any other or unknown task
# type is also chunked. Values are the `TASKS` enum in open_webui/constants.py.
_CHUNKING_EXEMPT_TASKS = frozenset({'title_generation', 'tags_generation', 'follow_up_generation'})


def _payload_task_type(payload):
    """The background-task type from `metadata.task` (see `TASKS` in
    open_webui.constants), or None for an ordinary chat turn. Defensive about
    a non-dict `metadata` the same way `_payload_chat_id` is."""
    metadata = payload.get('metadata')
    task = metadata.get('task') if isinstance(metadata, dict) else None
    return task if isinstance(task, str) else None


def _last_index_by_role(messages, role):
    """Index of the last message with `role`, or -1 when there is none.

    Must match `_find_last_user_index` / `_find_last_assistant_index` in
    `pii_filter_pipeline.py`, which decide which messages the pipeline runs
    NER on."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, dict) and m.get('role') == role:
            return i
    return -1


def _ner_priced_indices(messages):
    """Indices of the messages the pipeline runs full NER on: the last user
    message and the last assistant message (`ner_indices` in
    `pii_filter_pipeline.py`).

    Only these messages are slow to mask, at `PII_INLET_CHARS_PER_SECOND`.
    Every other message is re-masked from the vault by a regex, which is fast
    at any length."""
    return sorted(
        i
        for i in {
            _last_index_by_role(messages, 'user'),
            _last_index_by_role(messages, 'assistant'),
        }
        if i >= 0
    )


def _chunkable_message_indices(payload):
    """Indices of the messages to split into chunks: those returned by
    `_ner_priced_indices` whose content is longer than `PII_INLET_CHUNK_CHARS`.

    Older oversized messages are not chunked. The payload carries the
    conversation's original, unmasked text, so a long paste is sent again on
    every later turn. Chunking it each time would re-mask the whole history on
    every turn, and because chunked characters count against
    `PII_INLET_TOTAL_BUDGET_S`, a long enough history would make every new
    message in that chat be refused. Older messages are instead sent whole in
    the skeleton, where the pipeline re-masks them from the vault by regex.
    Limiting chunking to at most two messages keeps each turn's cost bounded
    by that turn's text.

    The last assistant message is included as well as the last user message.
    An oversized assistant reply left in the skeleton would get full NER inside
    the single skeleton POST and could exceed its socket-read timeout.
    """
    messages = payload.get('messages') or []
    return [
        i
        for i in _ner_priced_indices(messages)
        if isinstance(messages[i], dict)
        and isinstance(messages[i].get('content'), str)
        and len(messages[i]['content']) > PII_INLET_CHUNK_CHARS
    ]


def _payload_chat_id(payload):
    """The payload's chat id, from a top-level `chat_id` or, more commonly,
    `metadata.chat_id`. Returns None unless one of them is a non-empty string.
    """
    metadata = payload.get('metadata')
    for candidate in (
        payload.get('chat_id'),
        metadata.get('chat_id') if isinstance(metadata, dict) else None,
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _shifted_pii_detections(response, offset):
    """Return `metadata.pii_detections_public` from one inlet response, with
    every span shifted by `offset` characters.

    `pii_detections_public` is the `[{type, start, end}]` list the pipeline
    attaches for the PII card; it contains no PII values. For a chunk response,
    `offset` is the chunk's position in the original message, so the spans
    index the unsplit message. For the skeleton response, `offset` is 0.

    Never raises. An entry that is not a dict, or whose `start`/`end` is not an
    int, is skipped, so a malformed response from the pipeline cannot fail a
    request whose masking already succeeded. `bool` is rejected explicitly
    because it is a subclass of `int`.
    """
    metadata = response.get('metadata') if isinstance(response, dict) else None
    detections = metadata.get('pii_detections_public') if isinstance(metadata, dict) else None
    if not isinstance(detections, list):
        return []
    shifted = []
    for d in detections:
        if not isinstance(d, dict):
            continue
        start, end = d.get('start'), d.get('end')
        if not isinstance(start, int) or isinstance(start, bool):
            continue
        if not isinstance(end, int) or isinstance(end, bool):
            continue
        # `type` must be a string because `_mask_oversized_via_chunks` puts
        # `(type, start, end)` in a set to de-duplicate. A non-hashable `type`
        # (a list or dict) would raise TypeError after masking succeeded and
        # turn the request into a "masking unavailable" refusal.
        if not isinstance(d.get('type'), str):
            continue
        shifted.append({'type': d['type'], 'start': start + offset, 'end': end + offset})
    return shifted


async def _mask_oversized_via_chunks(session, url, key, filter_id, payload, user_with_valves, on_progress):
    """Mask a payload in which a message is too long for a single inlet call.

    The messages from `_chunkable_message_indices` are split into chunks. First
    a "skeleton" call sends the whole conversation with those messages' content
    replaced by an empty string. They are blanked, not removed: removing a
    message would change the last-user and last-assistant indices the pipeline
    uses to pick which messages get NER. The chunks are then masked
    concurrently and joined back in their original order.

    Concurrent chunks are safe: `ThreadVault.get_placeholder` in the pipeline
    is an atomic get-or-create, so the same value in two chunks gets the same
    placeholder. Only the placeholder numbering may differ from a sequential
    run, and nothing depends on it.

    Fails closed. `PiiMaskingUnavailableError` is raised when a chunk still
    fails after `PII_INLET_CHUNK_RETRIES` attempts, when masking takes longer
    than `PII_INLET_TOTAL_BUDGET_S`, or when the skeleton response cannot be
    matched to the request. A deliberate refusal from the pipeline propagates
    as its own `HTTPException`. The unmasked payload is never returned.

    Before any POST, the request is refused in three cases:

      * `estimated_masking_seconds` exceeds `PII_INLET_TOTAL_BUDGET_S`. The
        skeleton POST runs once, so its NER work is charged at the base rate.
        The chunk POSTs run concurrently and are charged at the speedup rate.
        Refusing up front avoids making the user wait for the deadline only to
        be refused.
      * The skeleton's NER work alone would not finish within
        `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ` (times
        `PII_INLET_SKELETON_SAFETY_MARGIN`). The skeleton is one POST and is
        limited by the socket-read timeout, which is shorter than the total
        budget.
      * The payload has no chat id (see `_payload_chat_id`). Without one, the
        pipeline creates a new temporary vault for each call, so every chunk
        would number placeholders from 1, different people would share
        `[PERSON_1]` in the joined prompt, and the outlet could not restore
        them. A shared synthetic id is not used instead because its vault rows
        would never be deleted with a chat. Single-call masking is not
        affected, since one call has only one vault.

    The skeleton is charged only for the NER-priced messages it still carries
    (see `_ner_priced_indices`), not for its full length, because the pipeline
    re-masks all other messages by regex. Known limitation: the pipeline
    applies that shortcut only when the chat's vault is not empty. If masking
    is switched on mid-conversation, the pipeline runs NER on the whole history
    and this estimate is too low. This is accepted: the request still fails
    closed, a payload without oversized messages has the same socket-read
    limit, and charging the whole history in every case would refuse ordinary
    requests in chats with a long history.
    """
    messages = payload.get('messages') or []
    indices = _chunkable_message_indices(payload)
    oversized = set(indices)
    chunked_chars = sum(
        len(m['content']) for i, m in enumerate(messages) if i in oversized and isinstance(m.get('content'), str)
    )
    # Characters in the skeleton that get NER: the NER-priced messages that
    # were short enough not to be chunked. This is at most
    # 2 * `PII_INLET_CHUNK_CHARS`, so with the default settings the socket-read
    # guard below never fires. Keep it anyway: it becomes necessary when
    # 2 * `PII_INLET_CHUNK_CHARS` exceeds `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ *
    # PII_INLET_SKELETON_SAFETY_MARGIN * PII_INLET_CHARS_PER_SECOND`, for example
    # after raising the chunk size or lowering the socket-read timeout.
    skeleton_chars = sum(
        len(messages[i]['content'])
        for i in _ner_priced_indices(messages)
        if i not in oversized and isinstance(messages[i], dict) and isinstance(messages[i].get('content'), str)
    )

    if estimated_masking_seconds(skeleton_chars, chunked_chars) > PII_INLET_TOTAL_BUDGET_S:
        raise PiiMaskingUnavailableError(
            'This message is too long to mask safely. Shorten it, or attach the text as a file instead.'
        )

    if (
        AIOHTTP_CLIENT_TIMEOUT_SOCK_READ is not None
        and skeleton_chars / PII_INLET_CHARS_PER_SECOND
        > AIOHTTP_CLIENT_TIMEOUT_SOCK_READ * PII_INLET_SKELETON_SAFETY_MARGIN
    ):
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

    jobs = []  # (message_index, piece_index, piece_offset, text)
    for i in indices:
        for piece_index, (piece_offset, piece) in enumerate(split_text_for_pii(messages[i]['content'])):
            jobs.append((i, piece_index, piece_offset, piece))

    total = len(jobs)
    done = 0
    _t0 = time.time()
    retry_stats: dict = {'retries': 0}

    # Report 0 of `total` before the first POST. The skeleton call runs to
    # completion before any chunk starts and can take a long time on a cold
    # pipeline, so without this the user sees no progress until the first chunk
    # finishes.
    #
    # Exceptions are caught: progress is informational and must not fail masking.
    if on_progress is not None:
        try:
            on_progress(0, total)
        except Exception as e:  # noqa: BLE001 — diagnostics must not break masking
            log.debug(f'[pii_chunking] could not emit the opening progress event: {e}')
    semaphore = asyncio.Semaphore(PII_INLET_CONCURRENCY)
    results: dict[tuple[int, int], str] = {}
    # Each chunk's `metadata.pii_detections_public`, keyed like `results`, with
    # spans already shifted to index the original unsplit message (see
    # `_shifted_pii_detections`).
    piece_detections: dict[tuple[int, int], list] = {}

    async def _mask_piece(msg_index, piece_index, piece_offset, piece):
        nonlocal done
        body = {
            **payload,
            'messages': [{**messages[msg_index], 'content': piece}],
        }
        out = await _post_inlet_with_retry(
            session,
            url,
            key,
            filter_id,
            {'user': user_with_valves, 'body': body},
            semaphore=semaphore,
            stats=retry_stats,
        )
        results[(msg_index, piece_index)] = out['messages'][0]['content']
        piece_detections[(msg_index, piece_index)] = _shifted_pii_detections(out, piece_offset)

        # Progress is reported after the retry loop has returned and the result
        # is stored. An exception from the callback is therefore never treated
        # as a chunk failure, which would re-send an already-masked chunk and
        # push `done` past `total`.
        done += 1
        if on_progress is not None:
            on_progress(done, total)

    async def _run_all():
        skeleton_out = await _post_inlet_with_retry(
            session,
            url,
            key,
            filter_id,
            {'user': user_with_valves, 'body': skeleton},
            stats=retry_stats,
        )
        # `asyncio.gather` raises the first exception but leaves the other tasks
        # running. After the request has failed, those tasks would keep sending
        # POSTs to the pipeline, writing vault rows for a prompt that is never
        # sent, and calling `on_progress` after the user has seen the error.
        # So on the first failure every task is cancelled, and the cancellation
        # is awaited so in-flight aiohttp requests are closed before this
        # returns.
        #
        # `BaseException` also catches the `CancelledError` raised when the
        # `wait_for` deadline cancels this coroutine; the same cleanup runs.
        #
        # Explicit tasks are used instead of `asyncio.TaskGroup` because a
        # TaskGroup wraps errors in an `ExceptionGroup`. The call site relies on
        # `except HTTPException` so a deliberate pipeline refusal reaches the
        # user with its own status. Re-raising here keeps the original type.
        tasks = [asyncio.create_task(_mask_piece(*job)) for job in jobs]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
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

    # Log the masking time on its own, so a slow turn can be attributed to
    # masking, retries or the model.
    log.info(
        '[pii_chunking] masked %d chunks in %.1fs (%d retries)',
        total,
        time.time() - _t0,
        retry_stats['retries'],
    )

    # Fail closed if the skeleton response has no `messages` or a different
    # number of messages than the request. Falling back would send the
    # original unmasked content, and a length mismatch would put a masked chunk
    # into the wrong message via `out_messages[i]`.
    out_messages = out.get('messages')
    if not out_messages or len(out_messages) != len(messages):
        raise PiiMaskingUnavailableError()
    out_messages = list(out_messages)
    for i in indices:
        # The piece count comes from `jobs`, not from the keys in `results`.
        # If a future change tolerated partial failures, counting `results`
        # would silently drop the missing pieces from the message. Instead
        # `results[(i, p)]` raises KeyError for a missing piece, and the call
        # site turns that into `PiiMaskingUnavailableError`.
        pieces = [results[(i, p)] for p in range(sum(1 for j in jobs if j[0] == i))]
        out_messages[i] = {**out_messages[i], 'content': ''.join(pieces)}

    # The pipeline's `pii_detections_public` covers only the last user message.
    # When the conversation's final message was not chunked, the skeleton
    # response already has the right detections. When it was chunked, the
    # skeleton sent it empty and reports none for it, so that message's chunk
    # detections are merged in. Detections from other chunked messages are not
    # added, matching what a single call reports.
    merged_detections = []
    seen_keys = set()

    def _merge(detections):
        for d in detections:
            key = (d.get('type'), d['start'], d['end'])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged_detections.append(d)

    _merge(_shifted_pii_detections(out, 0))
    last_index = len(messages) - 1
    if last_index in oversized:
        piece_count = sum(1 for j in jobs if j[0] == last_index)
        for p in range(piece_count):
            _merge(piece_detections.get((last_index, p), []))

    out_metadata = out.get('metadata')
    final_metadata = {
        **(out_metadata if isinstance(out_metadata, dict) else {}),
        'pii_detections_public': merged_detections,
    }
    return {**out, 'messages': out_messages, 'metadata': final_metadata}


async def get_openai_connection(request, url_idx: int) -> tuple[str, str]:
    # Read from the per-request AppConfig (request.app.state.config), not the
    # module-level ConfigVar — this fork keeps the AppConfig backbone and every
    # other pipelines.py connection lookup resolves it this way (and tests
    # override it there). Upstream's version took no request and read the
    # per-key Config store; adapted here to Risk #1.
    base_urls = request.app.state.config.OPENAI_API_BASE_URLS or []
    api_keys = request.app.state.config.OPENAI_API_KEYS or []
    return base_urls[url_idx], api_keys[url_idx]


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
    # per-user, per-filter valves.
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

            url, key = await get_openai_connection(request, urlIdx)

            if not key:
                continue

            # Per-filter valves injection. Each filter gets its own
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
                    and _payload_task_type(payload) not in _CHUNKING_EXEMPT_TASKS
                    and _chunkable_message_indices(payload)
                ):
                    # Too much text for one call. NER processes about 240
                    # characters per second, so a single POST with a long
                    # message would exceed the socket-read timeout. Mask the
                    # long messages in concurrent chunks instead.
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
                    # Single-call path: non-PII filters, masking switched off,
                    # requests without an oversized message, and the tasks in
                    # `_CHUNKING_EXEMPT_TASKS`. It is retried too, so one
                    # socket-read timeout or 429 does not fail the request, for
                    # example while chunked masking of the same turn is keeping
                    # the pipeline busy. With `fail_closed=False` the last
                    # exception is re-raised unchanged, so the handlers below
                    # still decide per filter.
                    payload = await _post_inlet_with_retry(
                        session, url, key, filter['id'], request_data, fail_closed=False
                    )
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

            url, key = await get_openai_connection(request, urlIdx)

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
    base_urls = request.app.state.config.OPENAI_API_BASE_URLS

    return {
        'data': [
            {
                'url': base_urls[urlIdx],
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
        async with aiofiles.open(file_path, 'wb') as buffer:
            while chunk := await file.read(AIOHTTP_FILE_STREAM_CHUNK_SIZE):
                await buffer.write(chunk)

        url, key = await get_openai_connection(request, urlIdx)

        headers = {'Authorization': f'Bearer {key}'}

        async with aiohttp.ClientSession(trust_env=True) as session:
            form_data = aiohttp.FormData()

            async def pipeline_chunks():
                async with aiofiles.open(file_path, 'rb') as pipeline_file:
                    while chunk := await pipeline_file.read(AIOHTTP_FILE_STREAM_CHUNK_SIZE):
                        yield chunk

            form_data.add_field(
                'file',
                pipeline_chunks(),
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

        await publish_event(
            request,
            EVENTS.PIPELINE_UPLOADED,
            actor=user,
            subject_id=data.get('id') or filename,
            data={'url_idx': urlIdx, 'filename': filename},
        )
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
            await asyncio.to_thread(os.remove, file_path)


class AddPipelineForm(BaseModel):
    url: str
    urlIdx: int


@router.post('/add')
async def add_pipeline(request: Request, form_data: AddPipelineForm, user=Depends(get_admin_user)):
    response = None
    try:
        urlIdx = form_data.urlIdx

        url, key = await get_openai_connection(request, urlIdx)

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(
                f'{url}/pipelines/add',
                headers={'Authorization': f'Bearer {key}'},
                json={'url': form_data.url},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        await publish_event(
            request,
            EVENTS.PIPELINE_ADDED,
            actor=user,
            subject_id=data.get('id') or form_data.url,
            data={'url_idx': urlIdx, 'url': form_data.url},
        )
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

        url, key = await get_openai_connection(request, urlIdx)

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.delete(
                f'{url}/pipelines/delete',
                headers={'Authorization': f'Bearer {key}'},
                json={'id': form_data.id},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        await publish_event(
            request,
            EVENTS.PIPELINE_DELETED,
            actor=user,
            subject_id=form_data.id,
            data={'url_idx': urlIdx},
        )
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
        url, key = await get_openai_connection(request, urlIdx)

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
        url, key = await get_openai_connection(request, urlIdx)

        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                f'{url}/{pipeline_id}/valves',
                headers={'Authorization': f'Bearer {key}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                response.raise_for_status()
                data = await response.json()

        await publish_event(
            request,
            EVENTS.PIPELINE_VALVES_UPDATED,
            actor=user,
            subject_id=pipeline_id,
            data={'url_idx': urlIdx},
        )
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
        url, key = await get_openai_connection(request, urlIdx)

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
        url, key = await get_openai_connection(request, urlIdx)

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
