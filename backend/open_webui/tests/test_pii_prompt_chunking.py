"""Prompt-path PII chunking (TRAU-543).

A 50-page paste used to go to the inlet as ONE request, blow the 60 s socket
read budget and surface as "PII masking is currently unavailable". These tests
pin the replacement: many bounded, concurrent chunk calls, still fail-closed.

The external inlet is MOCKED — `aiohttp.ClientSession` is patched in
`open_webui.routers.pipelines`.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault('stripe', MagicMock())

import aiohttp  # noqa: E402

from open_webui.routers.pipelines import (  # noqa: E402
    PiiMaskingUnavailableError,
    process_pipeline_inlet_filter,
)
from open_webui.utils.pii_chunking import PII_INLET_CHUNK_CHARS  # noqa: E402

# NON-periodic on purpose (~13 500 chars -> many chunks). A literal `* 400`
# repeat makes every masked piece a rotation of the same repeating unit, so
# `"".join(sorted(pieces))` can reassemble back to the identical string and
# the reassembled-in-order test can't tell a shuffle from the real thing (the
# same failure mode Task 1's own fixture hit). The per-repetition counter
# breaks the periodicity while keeping the `OIB 12345678903` literal in every
# unit, so the fail-on-substring test still matches.
BIG = ''.join(f'Ivan Horvat {i}, OIB 12345678903. ' for i in range(400))


def _models():
    return {
        'gpt-4': {'id': 'gpt-4'},
        'pii_filter_pipeline': {
            'id': 'pii_filter_pipeline',
            'urlIdx': 0,
            'pipeline': {'type': 'filter', 'priority': 0, 'pipelines': ['*']},
        },
    }


def _request():
    r = MagicMock()
    r.app.state.config.OPENAI_API_BASE_URLS = ['http://pipeline-host']
    r.app.state.config.OPENAI_API_KEYS = ['secret-key']
    r.app.state.config.USER_PERMISSIONS = {'chat': {'pii_masking_enforced': False}}
    r.state = SimpleNamespace()
    return r


def _user():
    return SimpleNamespace(id='u1', email='t@e.com', name='T', role='user', settings=None)


def _payload(content):
    return {
        'model': 'gpt-4',
        'messages': [{'role': 'user', 'content': content}],
        'metadata': {'chat_id': 'c1'},
        'features': {'pii_masking': True},
    }


def _session(seen, fail_on=None, delay=0.0):
    """Mock inlet: uppercases content so masking is observable, records every
    request, and optionally fails when `fail_on` appears in the chunk."""

    def _post(url, *, headers, json, ssl):
        # SYNC on purpose: the caller does `async with session.post(...)`, so
        # post() must RETURN the context manager, not a coroutine.
        body = json['body']
        text = body['messages'][0]['content']
        seen.append(text)
        if fail_on is not None and fail_on in text:
            raise aiohttp.ClientConnectionError('boom')
        out = {**body, 'messages': [{'role': 'user', 'content': text.upper()}]}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=out)
        resp.raise_for_status = MagicMock()
        resp.content_type = 'application/json'

        async def _enter(_self=None):
            # _self: unittest.mock wraps a plain function assigned to a magic
            # dunder (`cm.__aenter__ = _enter`) as `func(self, *args, **kw)`
            # (see CPython unittest/mock.py `_get_method`), so the mock passes
            # `cm` itself as a positional arg here. Discarded — unused.
            if delay:
                await asyncio.sleep(delay)  # the await must live inside __aenter__
            return resp

        cm = MagicMock()
        cm.__aenter__ = _enter
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    s = MagicMock()
    s.post = _post
    scm = MagicMock()
    scm.__aenter__ = AsyncMock(return_value=s)
    scm.__aexit__ = AsyncMock(return_value=False)
    return scm


def _run(payload, **kw):
    return asyncio.run(process_pipeline_inlet_filter(_request(), payload, _user(), _models(), **kw))


def test_a_short_message_still_takes_exactly_one_call():
    """The chunked path must not tax normal chats: below the threshold the
    behaviour and the call count are exactly what they were before."""
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        out = _run(_payload('kratka poruka'))
    assert len(seen) == 1
    assert out['messages'][0]['content'] == 'KRATKA PORUKA'


def test_an_oversized_message_is_split_into_bounded_chunks():
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(_payload(BIG))
    chunks = [t for t in seen if t]  # the skeleton call sends ""
    assert len(chunks) > 1
    assert all(len(c) <= PII_INLET_CHUNK_CHARS for c in chunks)


def test_the_masked_message_is_reassembled_in_order_and_losslessly():
    """Chunks are masked concurrently, so completion order is arbitrary. The
    reassembled message must still be the pieces in DOCUMENT order, not
    completion order — otherwise the model reads a shuffled prompt.

    Dispatch order equals document order: jobs are created in piece order and
    `asyncio.gather` schedules each coroutine's synchronous prefix (up to its
    first real suspension) in that same order — verified against this mock,
    where `session.post(...)` runs synchronously before the first `await`
    inside `__aenter__`. Giving EARLIER-dispatched chunks a LONGER delay
    therefore makes completion order the exact reverse of document order: an
    implementation that assembled by completion order (or appended to a plain
    list as results arrived) instead of indexing by piece_index would produce
    a visibly scrambled string here, where a `delay=0.0` mock could not tell
    the difference (dispatch order would trivially equal completion order).
    """
    seen = []
    dispatched = []  # dispatch order of the real chunk POSTs (skeleton excluded)

    def _post(url, *, headers, json, ssl):
        body = json['body']
        text = body['messages'][0]['content']
        seen.append(text)
        out = {**body, 'messages': [{'role': 'user', 'content': text.upper()}]}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=out)
        resp.raise_for_status = MagicMock()
        resp.content_type = 'application/json'

        delay = 0.0
        if text:  # not the skeleton call, which always sends ""
            dispatch_index = len(dispatched)
            dispatched.append(dispatch_index)
            # Earlier dispatch -> longer sleep -> later completion. Floored so
            # this stays a real (positive) delay even if the chunk count grows.
            delay = max(0.05 * (12 - dispatch_index), 0.01)

        async def _enter(_self=None):
            # _self: see comment in _session() above.
            if delay:
                await asyncio.sleep(delay)
            return resp

        cm = MagicMock()
        cm.__aenter__ = _enter
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    s = MagicMock()
    s.post = _post
    scm = MagicMock()
    scm.__aenter__ = AsyncMock(return_value=s)
    scm.__aexit__ = AsyncMock(return_value=False)

    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=scm):
        out = _run(_payload(BIG))
    assert out['messages'][0]['content'] == BIG.upper()


def test_a_chunk_that_never_succeeds_fails_the_whole_request_closed():
    """Fail-closed: a partially masked prompt must never reach the LLM."""
    seen = []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen, fail_on='OIB 12345678903')
    ):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(_payload(BIG))


def test_exceeding_the_total_budget_fails_closed_rather_than_hanging():
    seen = []
    with (
        patch('open_webui.routers.pipelines.PII_INLET_TOTAL_BUDGET_S', 0.05),
        patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen, delay=0.5)),
    ):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(_payload(BIG))


def test_concurrency_is_bounded():
    """Unbounded fan-out at a scale-to-zero service buys cold starts, not speed."""
    inflight, peak = {'n': 0}, {'n': 0}

    def _post(url, *, headers, json, ssl):
        body = json['body']
        out = {**body, 'messages': [{'role': 'user', 'content': body['messages'][0]['content'].upper()}]}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=out)
        resp.raise_for_status = MagicMock()
        resp.content_type = 'application/json'

        async def _enter(_self=None):
            # _self: see comment in _session() above.
            # Count occupancy across the await, which is where overlap happens.
            inflight['n'] += 1
            peak['n'] = max(peak['n'], inflight['n'])
            await asyncio.sleep(0.01)
            inflight['n'] -= 1
            return resp

        cm = MagicMock()
        cm.__aenter__ = _enter
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    s = MagicMock()
    s.post = _post
    scm = MagicMock()
    scm.__aenter__ = AsyncMock(return_value=s)
    scm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch('open_webui.routers.pipelines.PII_INLET_CONCURRENCY', 3),
        patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=scm),
    ):
        _run(_payload(BIG))
    # Lower bound matters as much as the upper one: `peak['n'] <= 3` alone
    # passes for a fully serial implementation (peak stuck at 1), silently
    # reverting the entire point of this task (a 150k paste is ~625 s serial
    # against a 120 s budget) while the suite stays green.
    assert 1 < peak['n'] <= 3


def test_progress_is_reported_monotonically_and_completes():
    calls = []
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(_payload(BIG), on_progress=lambda done, total: calls.append((done, total)))
    assert calls, 'no progress reported for a chunked request'
    assert [d for d, _ in calls] == sorted(d for d, _ in calls)
    assert calls[-1][0] == calls[-1][1]


def test_masking_disabled_skips_the_chunked_path_entirely():
    """features.pii_masking=False is a valid opt-out; it must not pay for chunking."""
    seen = []
    payload = _payload(BIG)
    payload['features']['pii_masking'] = False
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(payload)
    assert len(seen) == 1


def test_oversized_message_without_chat_id_fails_closed_before_any_post():
    """Every chunk POST inherits `payload['metadata']`, and the pipeline mints
    a FRESH ephemeral thread per call when chat_id is missing — each chunk
    would land in its own PII vault, so distinct people would collapse onto a
    shared placeholder across the reassembled prompt. Refuse before issuing
    any POST rather than mint a shared synthetic id."""
    seen = []
    payload = _payload(BIG)
    payload['metadata'] = {}  # no chat_id, top-level or nested
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(payload)
    assert seen == []


def test_chat_path_emits_a_pii_masking_status_event():
    """The user waits ~65 s for a 50-page paste. Silence reads as a hang, so the
    wait must be visible; the final event must mark itself done or the shimmer
    never stops.

    Driven inside a running loop on purpose: the emitter schedules with
    `asyncio.create_task`, which has no loop to attach to outside one — the
    production caller is always inside `asyncio.gather`."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 2)
        on_progress(2, 2)
        await asyncio.sleep(0)  # let the scheduled tasks run
        await asyncio.sleep(0)

    asyncio.run(drive())

    assert [e['data']['action'] for e in events] == ['pii_masking', 'pii_masking']
    assert events[0]['data']['done'] is False
    assert events[-1]['data']['done'] is True
    assert events[-1]['data']['count'] == events[-1]['data']['total'] == 2


def test_progress_swallows_a_synchronously_raising_emitter():
    """If `event_emitter(...)` itself raises before returning a coroutine (a
    non-async callable, or one that blows up before yielding), `on_progress`
    must swallow it. The producer calls `on_progress` from inside
    `_mask_piece`'s retry `try` — a synchronous exception escaping here would
    be caught there as a transient chunk failure and cause a spurious re-POST
    of an already-masked chunk."""
    import open_webui.utils.middleware as M

    def emitter(event):
        raise RuntimeError('boom')

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 1)  # must not raise

    asyncio.run(drive())  # must not raise


def test_progress_retrieves_a_raising_coroutines_exception_via_the_done_callback():
    """The event is scheduled with `asyncio.create_task`, so `event_emitter`'s
    own body runs later, off the `on_progress` call stack — meaning an
    exception raised there does NOT propagate to the caller regardless of
    whether anything retrieves it. A bare "on_progress must not raise"
    assertion is therefore true even for the bare `asyncio.create_task(...)`
    call with no stored reference and no done-callback: Python only surfaces
    an un-retrieved task exception later, through the event loop's default
    exception handler, when the task is garbage-collected. What the
    strong-reference set + `_pii_progress_task_done` actually buy is that the
    exception gets RETRIEVED (`task.exception()`) instead of leaking to that
    default handler. Assert the handler is never invoked — that is the
    property this mechanism exists for."""
    import gc

    import open_webui.utils.middleware as M

    async def emitter(event):
        raise RuntimeError('boom')

    handler_calls = []

    async def drive():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: handler_calls.append(context))

        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 1)  # must not raise
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # The done-callback should already have discarded the task from the
        # module-level set by now. Drop whatever reference is left and force
        # a collection so an un-retrieved exception can't hide behind GC
        # timing — if the callback didn't run, this is what would surface it.
        M._pii_progress_tasks.clear()
        gc.collect()

    asyncio.run(drive())  # must not raise

    assert handler_calls == [], (
        "the coroutine's exception must be retrieved via the done-callback, "
        f"not surfaced to asyncio's default exception handler: {handler_calls}"
    )


def test_progress_swallows_a_malformed_total_raised_by_the_throttle_guard_itself():
    """`_should_emit_pii_progress` starts with `if done <= 1 or done >=
    total`: with `done=1` the `or` short-circuits before `total` is ever
    compared, so `done=1` can't exercise this. `done=2` forces the second
    operand to actually evaluate `done >= total`, so a non-comparable
    `total` (e.g. None) raises a `TypeError` from INSIDE the guard call
    itself — the exact statement whose position (inside vs. outside the
    `try`) matters: if a future producer change ever passed such a `total`,
    that must be swallowed like a scheduling failure, not propagate out of
    `on_progress` and into `_mask_piece`'s retry `try`, where it would look
    like a transient chunk failure and trigger a spurious re-POST of an
    already-masked chunk."""
    import open_webui.utils.middleware as M

    async def emitter(event):
        pass

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(2, None)  # must not raise — done=2 skips the done<=1 short-circuit

    asyncio.run(drive())  # must not raise


def test_progress_throttles_to_about_ten_events_and_always_emits_the_terminal_one():
    """Every status event triggers a non-atomic whole-chat-row rewrite
    (`Chats.add_message_status_to_chat_by_id_and_message_id` ->
    `update_chat_by_id`, no optimistic-concurrency check). Emitting per-chunk
    on a large paste means dozens of concurrent whole-row rewrites that can
    clobber each other — so completions must be throttled to roughly ten
    events, and the terminal one (which stops the shimmer) must never be
    among the dropped ones."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        for done in range(1, 101):
            on_progress(done, 100)
        for _ in range(200):
            await asyncio.sleep(0)

    asyncio.run(drive())

    counts = [e['data']['count'] for e in events]
    assert counts[0] == 1, 'first completion must always be reported'
    assert counts[-1] == 100, 'last event reported must be the terminal one'
    assert events[-1]['data']['done'] is True, 'terminal event must be marked done'
    assert 2 <= len(events) <= 11, f'expected roughly ten throttled events, got {len(events)}'


def test_progress_on_a_small_total_still_emits_first_and_last_without_dividing_by_zero():
    """`total // 10` is 0 for any `total < 10`; the throttle must guard
    against a modulo-by-zero there and still guarantee the first and terminal
    events are reported."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 3)
        on_progress(2, 3)
        on_progress(3, 3)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(drive())  # must not raise (no ZeroDivisionError)

    counts = [e['data']['count'] for e in events]
    assert counts[0] == 1
    assert counts[-1] == 3
    assert events[-1]['data']['done'] is True


def test_a_prompt_beyond_the_budget_is_refused_with_a_message_a_user_can_act_on():
    """Past the budget the honest answer is a refusal, not a five-minute wait.
    The text must say what to do — the old wording named an internal constant."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    payload = _payload('x' * (max_maskable_chars() + 1))
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        with pytest.raises(PiiMaskingUnavailableError) as excinfo:
            _run(payload)
    message = str(excinfo.value)
    assert 'MAX_' not in message and 'CHARS' not in message
    assert 'too long' in message.lower() or 'shorten' in message.lower()
    assert seen == [], 'refused requests must not touch the pipeline at all'


def test_a_pure_paste_at_exactly_the_budget_boundary_is_allowed_through():
    """The other half of the boundary: a lone paste at exactly
    `max_maskable_chars()` characters (no other history, so skeleton_chars=0)
    must NOT be refused — it should proceed and issue POSTs."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    payload = _payload('x' * max_maskable_chars())
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(payload)  # must not raise
    assert seen, 'a within-budget paste must actually be masked, not refused'


def test_title_generation_task_is_exempt_from_chunking_even_when_oversized():
    """(finding #1) The pipeline skips NER entirely for title/tags/follow-up
    generation and re-masks via the deterministic vault regex alone --
    microseconds regardless of payload size -- so these must keep the single
    whole-payload call the pipeline expects. Chunking them (and applying the
    chunked path's size guard) would refuse exactly the large chats this
    ticket targets."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    payload = _payload('x' * (max_maskable_chars() + 1))
    payload['metadata']['task'] = 'title_generation'
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(payload)  # must not raise
    assert len(seen) == 1, 'an exempt task type must take exactly one POST, not be chunked'


def test_query_generation_task_still_chunks_when_oversized():
    """(finding #1) query_generation is deliberately NOT exempt: its output
    goes to an external service (RAG search), so the pipeline keeps full NER
    for it and chunking genuinely helps it survive that cost."""
    payload = _payload(BIG)
    payload['metadata']['task'] = 'query_generation'
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(payload)
    chunks = [t for t in seen if t]
    assert len(chunks) > 1, 'query_generation must still be chunked, not exempted'


def _detection_session(seen, detections_for):
    """Mock inlet like `_session`, but attaches
    `metadata.pii_detections_public` to each non-blank response, computed by
    `detections_for(piece_text)` -> list[dict] | None."""

    def _post(url, *, headers, json, ssl):
        body = json['body']
        text = body['messages'][0]['content']
        seen.append(text)
        out_metadata = {}
        detections = detections_for(text) if text else None
        if detections is not None:
            out_metadata['pii_detections_public'] = detections
        out = {**body, 'messages': [{'role': 'user', 'content': text.upper()}], 'metadata': out_metadata}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=out)
        resp.raise_for_status = MagicMock()
        resp.content_type = 'application/json'

        async def _enter(_self=None):
            return resp

        cm = MagicMock()
        cm.__aenter__ = _enter
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    s = MagicMock()
    s.post = _post
    scm = MagicMock()
    scm.__aenter__ = AsyncMock(return_value=s)
    scm.__aexit__ = AsyncMock(return_value=False)
    return scm


def test_chunked_detections_merge_with_document_relative_offsets():
    """(finding #5) Each chunk's own `pii_detections_public` must be shifted
    by that piece's offset into the message and merged into the returned
    metadata -- otherwise the card shows nothing for a message where the
    entities were actually found."""
    marker = 'OIB 12345678903'

    def _detections_for(text):
        idx = text.find(marker)
        return [{'type': 'HR_OIB', 'start': idx, 'end': idx + len(marker)}] if idx != -1 else []

    seen = []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession',
        return_value=_detection_session(seen, _detections_for),
    ):
        out = _run(_payload(BIG))

    real_chunks = [t for t in seen if t]
    assert len(real_chunks) > 1, 'fixture must actually produce more than one chunk'

    detections = out['metadata']['pii_detections_public']
    assert len(detections) > 1, 'detections from more than one chunk must be merged'
    for d in detections:
        assert BIG[d['start'] : d['end']] == marker, (
            'a merged detection must index the ORIGINAL message, not the piece it was found in'
        )


def test_malformed_chunk_detections_are_dropped_without_failing_the_request():
    """(finding #5) A detection entry that is not a dict, or whose start/end
    are not plain ints (bool included -- a subclass of int but never a valid
    offset), must be skipped rather than crash an otherwise-successful
    request -- a malformed entry from an external service must not fail-open
    the whole masked request."""

    def _detections_for(_text):
        return [
            'not-a-dict',
            {'type': 'HR_OIB', 'start': True, 'end': 5},  # bool start
            {'type': 'HR_OIB', 'start': 0, 'end': 'nope'},  # non-int end
            {'type': 'HR_OIB', 'start': 0, 'end': 4},  # well-formed
        ]

    seen = []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession',
        return_value=_detection_session(seen, _detections_for),
    ):
        out = _run(_payload(BIG))  # must not raise

    detections = out['metadata']['pii_detections_public']
    assert detections, 'the one well-formed detection per chunk must still survive'
    for d in detections:
        assert set(d) == {'type', 'start', 'end'}
        assert isinstance(d['start'], int) and not isinstance(d['start'], bool)
        assert isinstance(d['end'], int) and not isinstance(d['end'], bool)


def test_one_bad_chunk_fails_the_whole_request_while_other_chunks_still_complete():
    """(finding #7) The property that actually matters is not "all chunks
    fail" (already covered by
    `test_a_chunk_that_never_succeeds_fails_the_whole_request_closed`, whose
    `fail_on` substring appears in every chunk of `BIG`) but "ONE bad chunk
    kills the whole request rather than yielding partially-masked text". A
    `return_exceptions=True` regression would still raise for the all-fail
    case but silently succeed here, so only THIS test can catch it."""
    sentinel = 'SENTINEL_ONLY_IN_ONE_CHUNK_7f3'
    # Prepended so it sits well inside the very first ~1800-char chunk, far
    # from any split boundary, and appears in the fixture exactly once.
    content = sentinel + ' ' + BIG
    assert content.count(sentinel) == 1

    completed = []
    seen = []

    def _post(url, *, headers, json, ssl):
        body = json['body']
        text = body['messages'][0]['content']
        seen.append(text)
        if text and sentinel not in text:
            completed.append(text)
        if sentinel in text:
            raise aiohttp.ClientConnectionError('boom')
        out = {**body, 'messages': [{'role': 'user', 'content': text.upper()}]}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=out)
        resp.raise_for_status = MagicMock()
        resp.content_type = 'application/json'

        async def _enter(_self=None):
            return resp

        cm = MagicMock()
        cm.__aenter__ = _enter
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    s = MagicMock()
    s.post = _post
    scm = MagicMock()
    scm.__aenter__ = AsyncMock(return_value=s)
    scm.__aexit__ = AsyncMock(return_value=False)

    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=scm):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(_payload(content))

    real_chunks = [t for t in seen if t]
    assert len(real_chunks) > 1, 'fixture must actually produce more than one chunk'
    assert completed, 'other chunks must have completed before the bad one failed the whole request'
    assert len(completed) < len({t for t in real_chunks}), (
        'the sentinel chunk must be among those attempted, or this does not exercise the partial-failure case'
    )


def _history_session(calls):
    """Mock inlet for MULTI-message payloads: echoes back every message
    uppercased, the way the real pipeline returns the whole conversation, and
    records each POST's message contents.

    `_session` collapses its response to a single message, which is fine for
    the one-message payloads above but makes a multi-message skeleton trip the
    `len(out_messages) != len(messages)` fail-closed check before the assertion
    under test is ever reached.
    """

    def _post(url, *, headers, json, ssl):
        body = json['body']
        calls.append([m.get('content') for m in body['messages']])
        out = {
            **body,
            'messages': [{**m, 'content': (m.get('content') or '').upper()} for m in body['messages']],
        }
        resp = MagicMock()
        resp.json = AsyncMock(return_value=out)
        resp.raise_for_status = MagicMock()
        resp.content_type = 'application/json'

        async def _enter(_self=None):
            return resp

        cm = MagicMock()
        cm.__aenter__ = _enter
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    s = MagicMock()
    s.post = _post
    scm = MagicMock()
    scm.__aenter__ = AsyncMock(return_value=s)
    scm.__aexit__ = AsyncMock(return_value=False)
    return scm


def _chunk_calls(calls):
    """The chunk POSTs among recorded calls: a chunk carries exactly one
    non-blank message. The skeleton is either the full conversation (more
    than one message) or, for a single-message payload, that one message
    BLANKED -- falsy either way."""
    return [c for c in calls if len(c) == 1 and c[0]]


def _turn(*messages):
    return {
        'model': 'gpt-4',
        'messages': [{'role': r, 'content': c} for r, c in messages],
        'metadata': {'chat_id': 'c1'},
        'features': {'pii_masking': True},
    }


def test_only_the_message_being_sent_is_chunked_not_the_whole_history():
    """THE regression this follow-up exists for. Chunking every oversized
    message in the payload re-splits and re-masks the entire history on every
    single turn: the observed counter climbed 15 -> 17 -> 19 across three
    turns (the third being a two-sentence prompt, whose growth came from the
    ASSISTANT's oversized reply), the work grew quadratically in turn count,
    and around turn 4 the accumulated estimate crossed
    `PII_INLET_TOTAL_BUDGET_S` and the chat refused itself PERMANENTLY -- even
    for a one-word message.

    Only the message being sent this turn can contain PII that has never been
    through the pipeline; older ones were NER'd and vaulted on the turn they
    were typed, and `pii_filter_pipeline.py` re-masks them from the vault by
    regex (microseconds) rather than re-running NER. Chunk count must
    therefore depend on the CURRENT message alone, not on conversation
    length."""
    calls = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('user', BIG)))
    first_turn_chunks = len(_chunk_calls(calls))
    assert first_turn_chunks > 1, 'fixture must actually be chunked'

    calls.clear()
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('user', BIG), ('assistant', 'Short reply.'), ('user', 'Two short sentences. That is all.')))
    assert len(calls) == 1, (
        'a short message must take exactly one POST no matter how much oversized history sits behind it'
    )

    calls.clear()
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(
            _turn(
                ('user', BIG),
                ('assistant', 'Short reply.'),
                ('user', 'Two short sentences. That is all.'),
                ('assistant', 'Another short reply.'),
                ('user', BIG),
            )
        )
    assert len(_chunk_calls(calls)) == first_turn_chunks, (
        'the same paste must cost the same number of chunks on turn 3 as on turn 1'
    )


def test_oversized_history_is_left_whole_for_the_pipelines_own_vault_remask():
    """The companion property: an oversized message that is NOT the one being
    sent must reach the pipeline at FULL length in the skeleton, not blanked
    and reassembled from chunks. Blanking it would hide from the pipeline the
    history it re-masks from the vault."""
    calls = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('user', BIG), ('assistant', 'Short reply.'), ('user', 'And a short follow-up.')))
    assert len(calls) == 1
    assert calls[0][0] == BIG, 'oversized history must be sent whole, not blanked'


def test_a_long_conversation_with_an_oversized_paste_is_not_refused_by_history_length():
    """Ordinary history must not consume the masking budget. The pipeline runs
    full NER only on the last user and last assistant message
    (`ner_indices` in `pii_filter_pipeline.py`); every other history entry
    stops at the deterministic vault re-mask, which is regex and costs
    microseconds regardless of length. Charging history at the NER rate
    refused exactly the established chats this ticket set out to unblock."""
    history = []
    for _ in range(10):
        history.append(('user', 'a' * 1500))
        history.append(('assistant', 'b' * 1500))
    payload = _turn(*history, ('user', BIG))

    calls = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(payload)  # must not raise
    assert _chunk_calls(calls), 'the paste must actually be masked, not refused'


def test_an_oversized_assistant_reply_is_chunked_rather_than_left_to_blow_the_skeleton_post():
    """The last ASSISTANT message is NER'd by the pipeline too, so leaving an
    oversized one whole in the skeleton would pay its full NER cost inside the
    single sequential skeleton POST and blow that call's 60s socket read --
    bricking the chat the same way, only triggered by the model instead of the
    user. It is chunked alongside the message being sent. The set stays
    bounded at two messages, so this cannot accumulate across turns."""
    calls = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('user', 'hi'), ('assistant', BIG)))
    assistant_only = len(_chunk_calls(calls))
    assert assistant_only > 1, 'an oversized assistant reply must be chunked'

    calls.clear()
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('assistant', BIG), ('user', BIG)))
    assert len(_chunk_calls(calls)) == 2 * assistant_only, 'both NER-priced messages are chunked, and only those two'

    # An EARLIER oversized assistant reply is not NER'd by the pipeline and
    # must stay out of the chunked set.
    calls.clear()
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('assistant', BIG), ('user', 'ok'), ('assistant', 'Short reply.'), ('user', BIG)))
    assert len(_chunk_calls(calls)) == assistant_only, 'only the LAST assistant reply is chunked'


def test_the_two_ner_priced_messages_together_can_still_exceed_the_total_budget():
    """The budget guard survives the narrowing: a long assistant reply and a
    long paste are both chunked, both charged, and their sum is still checked
    against `PII_INLET_TOTAL_BUDGET_S` before any POST goes out."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    half = max_maskable_chars() // 2 + 1
    payload = _turn(('assistant', 'r' * half), ('user', 'b' * half))

    calls = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(payload)
    assert calls == [], 'refused requests must not touch the pipeline at all'


def test_a_detection_with_an_unhashable_type_is_dropped_rather_than_crashing_the_merge():
    """`_shifted_pii_detections` validated start/end but passed `type`
    through untouched, and the de-duplication key `(type, start, end)` goes
    into a set. A non-hashable `type` from the external pipeline therefore
    raised TypeError AFTER masking had already succeeded, turning a good
    response into a spurious "masking unavailable" refusal."""

    def _detections_for(_text):
        return [
            {'type': ['HR_OIB'], 'start': 0, 'end': 4},  # unhashable -> must be dropped
            {'type': 'HR_OIB', 'start': 0, 'end': 4},  # well-formed
        ]

    seen = []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession',
        return_value=_detection_session(seen, _detections_for),
    ):
        out = _run(_payload(BIG))  # must not raise

    detections = out['metadata']['pii_detections_public']
    assert detections, 'the well-formed detection must survive'
    for d in detections:
        assert isinstance(d['type'], str)
