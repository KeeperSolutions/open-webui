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
    (top-level, else `metadata.features`). Returns
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
    chunked path can issue the same call many times. Behaviour is
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



async def _post_inlet_with_retry(
    session, url, key, filter_id, request_data, *, semaphore=None, stats=None, fail_closed=True
):
    """One inlet POST, retried on TRANSIENT failure, refused on a deliberate one.

    Two failure families reach us through `_post_inlet_once`, and they must not
    be treated alike:

      * a deliberate refusal (400 / 401 / 403 / 422) — the pipeline evaluated
        the request and said no. Retrying triples the load and returns the same
        answer, so it propagates untouched and reaches the user as its own
        status;
      * platform back-pressure (429, transient 5xx, the Cloudflare 52x) — the
        service has no free instance yet and is starting one. That is "come
        back", not "no".

    Both arrived as `HTTPException`, and the chunk loop re-raised every one of
    them as deliberate. A single 429 therefore killed a whole 94-chunk masking
    run at 25/94, discarding two minutes of already-masked work. The SKELETON
    call had no retry at all, which is worse: it runs first, so one 429 there
    failed every request outright.

    `LLM_RETRY_RETRYABLE_STATUS` rather than a second hand-kept list — it
    already enumerates exactly these and is operator-overridable, and the PII
    pipeline is an upstream HTTP service like any other here.

    The two backoff schedules differ on purpose. A dropped connection clears in
    milliseconds; a 429 means an instance is booting, which takes seconds, so
    the linear schedule spent every attempt inside the first 1.5 s and re-failed
    against the same cold fleet. `semaphore` is held around the POST only, never
    across a backoff, so a sleeping retry does not hold a slot other chunks
    could use.
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
            # WARNING, not debug: this is the difference between "the pipeline
            # is fine and something else is slow" and "we are spending the whole
            # run waiting for instances to boot". Retrying used to be invisible,
            # so a run that got slower had no evidence either way.
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
    # What an exhausted retry means depends on WHO is asking, so the caller
    # says. The chunked masking path has no fallback — a chunk that never
    # succeeded means the message cannot be masked, which is
    # `PiiMaskingUnavailableError`. The ordinary single-call path is shared by
    # EVERY filter, including non-PII ones where a connection error must stay
    # best-effort passthrough (a telemetry outage must not block chat); it
    # therefore wants the original exception back so the per-filter fail-closed
    # logic in `process_pipeline_inlet_filter` keeps making that decision.
    if fail_closed:
        raise PiiMaskingUnavailableError() from last_exc
    raise last_exc

# Background-task types (carried on `metadata.task`) for which the external
# pipeline skips NER entirely and re-masks via the deterministic vault regex
# alone — mirrors `_LLM_FACING_SKIP_NER_TASKS` in the pipeline's own
# `pii_filter_pipeline.py`. That re-mask costs microseconds regardless of
# payload size, so these three keep the ORIGINAL single whole-payload call:
# both the chunked path and its size guard exist solely to survive NER's flat
# ~240 chars/s ceiling, which these payloads never pay. Chunking them anyway
# would refuse large title/tags/follow-up prompts that succeeded before this
# branch, and would turn one cheap call into ~20 for smaller ones.
#
# `query_generation` and `image_prompt_generation` are deliberately NOT
# exempt: their output goes to an EXTERNAL service (RAG search / image
# backend), so the pipeline keeps full NER for them, and chunking genuinely
# helps those large payloads survive it. Every other/unknown task type also
# keeps chunking — this is an allowlist, not `metadata.task`-generic.
# Strings are the OpenWebUI `TASKS` enum values (open_webui/constants.py).
_CHUNKING_EXEMPT_TASKS = frozenset({'title_generation', 'tags_generation', 'follow_up_generation'})


def _payload_task_type(payload):
    """The background-task type from `metadata.task` (see `TASKS` in
    open_webui.constants), or None for an ordinary chat turn. Defensive about
    a non-dict `metadata` the same way `_payload_chat_id` is."""
    metadata = payload.get('metadata')
    task = metadata.get('task') if isinstance(metadata, dict) else None
    return task if isinstance(task, str) else None


def _last_index_by_role(messages, role):
    """Index of the last message with `role`, or -1 when there is none —
    mirroring `_find_last_user_index` / `_find_last_assistant_index` in
    `pii_filter_pipeline.py`, whose answers decide what that service charges
    us for."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, dict) and m.get('role') == role:
            return i
    return -1


def _ner_priced_indices(messages):
    """The indices `pii_filter_pipeline.py` will run full NER on: the last
    user and the last assistant message (its `ner_indices`). These are the
    only messages whose length costs `PII_INLET_CHARS_PER_SECOND` — every
    other history entry stops at the deterministic vault re-mask, a regex
    costing microseconds regardless of length."""
    return sorted(
        i
        for i in {
            _last_index_by_role(messages, 'user'),
            _last_index_by_role(messages, 'assistant'),
        }
        if i >= 0
    )


def _chunkable_message_indices(payload):
    """The messages this request must split into sub-chunks: the two the
    pipeline actually runs NER on (`_ner_priced_indices`), and only those of
    them whose own content exceeds the per-call budget.

    Deliberately NOT "every oversized message in the payload", which is what
    this returned first and which was wrong in a way that compounded:

      * The payload carries the conversation's ORIGINAL text (masking is
        undone on the way out), so an oversized paste comes back in full on
        every subsequent turn. Chunking all of them re-split and re-masked
        the entire history each turn — the progress counter climbed
        15 -> 17 -> 19 across three turns, the third of which was a
        two-sentence prompt whose growth came from the ASSISTANT's oversized
        reply. Work grew quadratically in turn count.
      * Worse, `_mask_oversized_via_chunks` charges every chunked character
        against `PII_INLET_TOTAL_BUDGET_S`. Around turn 4 the accumulated
        history alone crossed the budget and the chat refused ITSELF —
        permanently, for any message, including a one-word one.

    Scoping to `_ner_priced_indices` fixes both: the set is at most two
    messages, so per-turn cost is bounded by THIS turn's text and cannot
    accumulate across turns. Older oversized messages are sent whole in the
    skeleton, where the pipeline re-masks them from the vault by regex — see
    its `ner_indices` / `remask_pattern` seam.

    Both NER'd messages are included, not just the user's: an oversized
    assistant reply left in the skeleton would pay full NER inside the single
    sequential skeleton POST and could blow its socket read — bricking the
    chat exactly as before, only triggered by the model rather than the user.
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


def _shifted_pii_detections(response, offset):
    """Defensively extract `metadata.pii_detections_public` (the PII-free
    `[{type, start, end}]` mirror the pipeline attaches for the card — see
    `pii_filter_pipeline.py`) from one inlet response, shifting every span by
    `offset` characters. Used for both a chunk response (`offset` = that
    piece's document offset, so spans index the ORIGINAL unsplit message) and
    the skeleton response (`offset=0`).

    Never raises: a detection entry that is not a dict, or whose `start`/
    `end` are not plain ints, is skipped rather than crashing the request — a
    security-facing card being wrong in one entry is bad, but a malformed
    entry from an external service must never be able to fail-open the whole
    masked request. `bool` is excluded explicitly because it is a subclass of
    `int` in Python and is never a valid character offset.
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
        # `type` is validated for the same reason, plus one of its own: the
        # de-duplication key in `_mask_oversized_via_chunks` is
        # `(type, start, end)` and goes into a set, so a non-hashable `type`
        # (a list, a dict) from the external service would raise TypeError
        # AFTER masking had already succeeded — turning a good response into
        # a spurious "masking unavailable" refusal.
        if not isinstance(d.get('type'), str):
            continue
        shifted.append({'type': d['type'], 'start': start + offset, 'end': end + offset})
    return shifted


async def _mask_oversized_via_chunks(session, url, key, filter_id, payload, user_with_valves, on_progress):
    """Mask a payload whose message(s) exceed the per-call budget.

    One skeleton call carries the conversation with the oversized content
    BLANKED (not removed — see Key Decision 5: deleting a message shifts the
    pipeline's last-user / last-assistant indices and silently changes which
    history entries get NER). That content is then masked as independent
    sub-chunks, concurrently, and spliced back in document order. Only the
    message being sent this turn is ever chunked — see
    `_chunkable_message_indices` for why chunking history compounded into a
    chat that permanently refused itself.

    Concurrency is safe: `ThreadVault.get_placeholder` is atomic get-or-mint and
    idempotent under concurrency, so racing chunks that contain the same value
    receive the same placeholder. Only the assigned number may differ from
    sequential order, which nothing depends on.

    FAIL-CLOSED: any chunk still failing after `PII_INLET_CHUNK_RETRIES`, and any
    overrun of `PII_INLET_TOTAL_BUDGET_S`, raises `PiiMaskingUnavailableError`.

    Refuses outright, before issuing any POST, when `estimated_masking_seconds`
    exceeds `PII_INLET_TOTAL_BUDGET_S`. The estimate is split in two: the
    skeleton POST runs once, sequentially (charged at 1x — no speedup applies
    to it), while the chunk POSTs benefit from concurrency (charged at the
    measured speedup). Applying the speedup to the whole payload would let a
    borderline request slip past the guard and straight into the
    `PII_INLET_TOTAL_BUDGET_S` timeout below — paying the wall-clock cost
    first and refusing anyway. Refusing up front is the honest answer.

    The skeleton's share is NOT its total character count. Measured cost
    (~`PII_INLET_CHARS_PER_SECOND`) is the cost of NER, and
    `pii_filter_pipeline.py` runs NER on exactly two messages: the last user
    and the last assistant one (`ner_indices`, mirrored here by
    `_ner_priced_indices`). Every other history entry stops at the
    deterministic vault re-mask — a regex, microseconds, independent of
    length. Charging the whole history at the NER rate is what refused an
    established chat with a modest paste (~12k of history plus 5k pasted)
    that in reality completes in about 21s.

    A second, narrower guard refuses when the skeleton's own NER share cannot
    fit inside one POST: the total-budget check bounds skeleton + chunked cost
    against `PII_INLET_TOTAL_BUDGET_S` (120s), but the skeleton is a single
    sequential call bound by `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ` (60s) — a much
    tighter ceiling. Without it such a request would pass the guard, run the
    skeleton POST, and die on ITS OWN socket read after 60s — the
    wait-then-refuse this branch exists to remove.

    ACCEPTED residual: the `ner_indices` narrowing is gated in the pipeline on
    `remask_pattern is not None`, i.e. on a non-empty vault for this thread.
    A chat whose vault is empty (PII masking switched on mid-conversation) has
    its whole history NER'd, and this estimate under-predicts. That case is
    not made worse by chunking — a payload with no oversized message never
    reaches this branch and hits the identical 60s socket read on its single
    POST — and it still fails CLOSED. Modelling it here instead would restore
    the over-charging above and refuse the common case to protect the rare one.

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
    indices = _chunkable_message_indices(payload)
    oversized = set(indices)
    chunked_chars = sum(
        len(m['content']) for i, m in enumerate(messages) if i in oversized and isinstance(m.get('content'), str)
    )
    # The skeleton's NER-priced share: whichever of the two NER'd messages is
    # small enough to have been left in it. See the docstring — everything
    # else in the skeleton is vault-re-masked by regex, not detected. With
    # today's constants it cannot exceed 2 * `PII_INLET_CHUNK_CHARS` (3 600
    # chars, ~15s — anything larger is chunked instead), so the socket-read
    # guard below cannot currently fire. It stays because it is the correct
    # invariant, not because it is live today: raising
    # `PII_INLET_CHUNK_CHARS` past `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ *
    # PII_INLET_SKELETON_SAFETY_MARGIN * PII_INLET_CHARS_PER_SECOND` makes it
    # load-bearing again, and silently losing it there is a 60s hang.
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

    # Announce the size BEFORE the first POST. Progress used to start at `1/N`,
    # reported when the first chunk completed — but the skeleton call below runs
    # to completion before any chunk starts, so on a cold pipeline (longer still
    # once platform back-pressure is retried rather than fatal) the user watched
    # a bare spinner for a minute with no sign that masking had begun. `total` is
    # already known here, so there is nothing to wait for.
    #
    # Guarded like every other call into this callback: progress is diagnostics
    # and must never be able to fail the masking path.
    if on_progress is not None:
        try:
            on_progress(0, total)
        except Exception as e:  # noqa: BLE001 — diagnostics must not break masking
            log.debug(f'[pii_chunking] could not emit the opening progress event: {e}')
    semaphore = asyncio.Semaphore(PII_INLET_CONCURRENCY)
    results: dict[tuple[int, int], str] = {}
    # Each chunk's `metadata.pii_detections_public` (finding #5), keyed the
    # same way as `results`, already shifted to index the ORIGINAL (unsplit)
    # message rather than the piece — see `_shifted_pii_detections`.
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

        # Finding #9: progress reporting lives OUTSIDE the retry, after the
        # result is already recorded. A callback that raises must never be
        # mistaken for a transient chunk failure — that would re-POST a chunk
        # that already succeeded, pushing `done` past `total`.
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
        # Structured, not bare `gather`: `asyncio.gather` propagates the FIRST
        # exception to its awaiter while leaving every sibling running. Once a
        # deliberate pipeline refusal or an exhausted retry has already failed
        # this request closed, those orphans go on issuing inlet POSTs against
        # a bottleneck that is often the very reason the first one failed,
        # writing vault rows for a prompt that will never be sent, and calling
        # `on_progress` — so a masking bar keeps advancing underneath an error
        # the user has already been shown. Cancel them on the first failure and
        # AWAIT the cancellation, so the in-flight aiohttp requests are actually
        # torn down before this returns rather than merely marked for it.
        #
        # `BaseException` covers the deadline too: `wait_for` cancels this
        # coroutine, and `gather` alone would then request its children's
        # cancellation without waiting for it to take effect.
        #
        # Explicit tasks rather than `asyncio.TaskGroup`: a TaskGroup re-raises
        # as an `ExceptionGroup`, which would break the `except HTTPException`
        # contract at the call site (a pipeline saying no ON PURPOSE must reach
        # the user as its own status, not as a generic masking outage). Re-
        # raising here preserves the original exception type exactly.
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

    # What the masking itself cost, separate from everything around it. Without
    # this a slower turn is unattributable: chunk time, retry backoff and the
    # model's own latency all land in the one number the user perceives.
    log.info(
        '[pii_chunking] masked %d chunks in %.1fs (%d retries)',
        total,
        time.time() - _t0,
        retry_stats['retries'],
    )

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

    # Finding #5: the skeleton response's own `pii_detections_public` is
    # correct ONLY for whichever message the skeleton call itself treated as
    # the last user message — right when that message was not oversized (it
    # then survived the skeleton at full length, unchanged). When the ACTUAL
    # last message of the conversation is itself oversized, the skeleton sent
    # it blanked (''), so the skeleton alone always reports zero detections
    # for it — exactly the false negative this fixes. Merge in the reassembled
    # chunks' detections ONLY when the last message is oversized: that mirrors
    # the pipeline's own single-call rule that `pii_detections_public` only
    # ever covers the conversation's last user message, so an earlier
    # (non-last) oversized message's detections are correctly never surfaced.
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
                    # One call cannot carry this much text. The inlet
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
                    # The ORDINARY single-call path — every non-oversized chat
                    # turn and every task generator (title / tags / follow-ups).
                    # It had no retry at all, so one socket-read timeout or one
                    # 429 failed it outright; observed live as a title
                    # generation dying on `SocketTimeoutError` while the chunked
                    # masking of the same turn was still saturating the pipeline.
                    # Retrying the chunked path but not this one left the most
                    # frequently taken path the most fragile.
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
