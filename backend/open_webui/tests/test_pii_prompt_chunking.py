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


def test_a_history_heavy_chat_is_refused_even_though_its_grand_total_is_under_the_paste_cap():
    """The skeleton POST carries every non-oversized message at full length,
    ONCE, sequentially — no concurrency speedup applies to it. A chat with
    enough ordinary history can bust the wall-clock budget on the skeleton
    call alone even while its grand total stays under `max_maskable_chars()`,
    because that cap only bounds a lone paste (skeleton_chars=0). Summing
    everything and applying the speedup to the whole payload (the earlier,
    wrong formula) would let this case slip past the guard and into the
    120 s `PII_INLET_TOTAL_BUDGET_S` timeout instead — refused either way, but
    only after paying the wait."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    # 20 ordinary turns, each under the 1800-char oversized threshold, so all
    # 20 land in the skeleton POST: 20 * 1500 = 30 000 chars / 240 chars/s =
    # 125 s on the skeleton call alone, already past the 120 s budget.
    history = [{'role': 'user', 'content': 'a' * 1500} for _ in range(20)]
    paste = {'role': 'user', 'content': 'b' * 2000}  # oversized -> chunked, not skeleton
    payload = {
        'model': 'gpt-4',
        'messages': history + [paste],
        'metadata': {'chat_id': 'c1'},
        'features': {'pii_masking': True},
    }
    total_chars = sum(len(m['content']) for m in payload['messages'])
    assert total_chars < max_maskable_chars(), (
        'the case only matters if the naive total-based cap would have let it through'
    )

    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(payload)
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
