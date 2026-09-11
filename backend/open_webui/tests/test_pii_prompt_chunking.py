"""Tests for PII masking of long prompts on the chat path.

A message above the chunk threshold is split into bounded pieces, masked by
concurrent pipeline calls and reassembled in order. If any chunk fails, the
whole request is refused, so partially masked text never reaches the model.

The external inlet is mocked by patching `aiohttp.ClientSession` in
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
from open_webui.utils.pii_chunking import (  # noqa: E402
    PII_INLET_CHUNK_CHARS,
    split_text_for_pii,
)

# About 12 300 characters, which splits into many chunks. Each repetition has
# its own counter so the text is not periodic: with a plain `* 400` repeat every
# chunk is a rotation of the same unit, and the in-order reassembly test could
# not tell a shuffled result from the correct one. Every repetition still
# contains `SSN 123-45-6789`, which the fail-on-substring test matches.
BIG = ''.join(f'John Doe {i}, SSN 123-45-6789. ' for i in range(400))


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
        # Synchronous on purpose: the caller does `async with session.post(...)`,
        # so post() must return the context manager, not a coroutine.
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
            # _self: unittest.mock calls a plain function assigned to a magic
            # method (`cm.__aenter__ = _enter`) with the mock as the first
            # argument, so `_self` receives `cm`. It is not used.
            if delay:
                await asyncio.sleep(delay)  # post() is synchronous, so the delay goes here
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
    """A message below the chunk threshold takes a single inlet call, so
    ordinary chats pay nothing for chunking."""
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
    """Chunks finish in arbitrary order, but the reassembled message follows
    document order, or the model would receive a shuffled prompt. The mock
    delays earlier-dispatched chunks longer, so completion order is the reverse
    of document order and assembling results as they arrive would fail here.
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
            # Earlier dispatch sleeps longer and so completes later. The floor
            # keeps the delay positive if the chunk count grows past 12.
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
        'open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen, fail_on='SSN 123-45-6789')
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
    """Chunk calls run concurrently but never more than `PII_INLET_CONCURRENCY`
    at once; unbounded fan-out to a scale-to-zero service causes cold starts,
    not speed-up."""
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
    # Check the lower bound too: `peak['n'] <= 3` alone also passes for a serial
    # implementation (peak of 1), and a serial run cannot mask a large paste
    # within the time budget.
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
    """With `features.pii_masking` set to False, even an oversized message takes
    a single call and skips chunking."""
    seen = []
    payload = _payload(BIG)
    payload['features']['pii_masking'] = False
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(payload)
    assert len(seen) == 1


def test_oversized_message_without_chat_id_fails_closed_before_any_post():
    """An oversized message without a chat_id is refused before any POST.
    Without a chat_id the pipeline creates a new PII vault for every call, so
    each chunk would get its own vault and different people could share one
    placeholder in the reassembled prompt."""
    seen = []
    payload = _payload(BIG)
    payload['metadata'] = {}  # no chat_id, top-level or nested
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(payload)
    assert seen == []


def test_chat_path_emits_a_pii_masking_status_event():
    """Masking progress is sent as `pii_masking` status events, and the final
    event is marked done so the loading animation stops.

    Runs inside an event loop because the emitter schedules events with
    `asyncio.create_task`, which needs a running loop."""
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
    """`on_progress` swallows an exception raised synchronously by the emitter.
    It is called inside `_mask_piece`'s retry block, so an escaping exception
    would count as a chunk failure and re-send an already masked chunk."""
    import open_webui.utils.middleware as M

    def emitter(event):
        raise RuntimeError('boom')

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 1)  # must not raise

    asyncio.run(drive())  # must not raise


def test_progress_retrieves_a_raising_coroutines_exception_via_the_done_callback():
    """An exception raised inside the scheduled emitter task is retrieved by
    the done-callback and never reaches the event loop's exception handler.
    Checking only that `on_progress` does not raise would pass even without the
    callback, because the task body runs later, outside that call."""
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

        # The done-callback should already have removed the task from the
        # module-level set. Clear any remaining references and force garbage
        # collection, so an unretrieved exception reaches the handler now
        # instead of at some later collection.
        M._pii_progress_tasks.clear()
        gc.collect()

    asyncio.run(drive())  # must not raise

    assert handler_calls == [], (
        "the coroutine's exception must be retrieved via the done-callback, "
        f"not surfaced to asyncio's default exception handler: {handler_calls}"
    )


def test_progress_swallows_a_malformed_total_raised_by_the_throttle_guard_itself():
    """A `TypeError` raised by the throttle check for a non-comparable `total`
    (such as None) is swallowed, so it cannot trigger a chunk retry. The test
    uses `done=2` because with `done=1` the check returns before comparing
    against `total`."""
    import open_webui.utils.middleware as M

    async def emitter(event):
        pass

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(2, None)  # must not raise; done=2 gets past the done <= 1 check

    asyncio.run(drive())  # must not raise


def test_progress_throttles_to_about_twenty_events_and_always_emits_the_terminal_one():
    """Progress events are throttled to about twenty per run, and the terminal
    event, which stops the loading animation, is always emitted. Each event
    rewrites the whole chat row without a concurrency check, so one event per
    chunk would let concurrent writes overwrite each other."""
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
    assert 2 <= len(events) <= 21, f'expected roughly twenty throttled events, got {len(events)}'


@pytest.mark.parametrize('total', [20, 25, 39, 40, 41, 59, 60, 99, 100, 150, 160])
def test_the_throttle_stays_bounded_in_every_band_not_just_multiples_of_twenty(total):
    """The throttle emits at most about twenty events for every `total`, not
    only for multiples of twenty. This requires rounding the stride up:
    rounding down gives a stride of 1 for totals 20 to 39, which emits every
    completion.
    """
    import open_webui.utils.middleware as M

    emitted = [done for done in range(1, total + 1) if M._should_emit_pii_progress(done, total)]

    assert len(emitted) <= 21, f'expected at most ~twenty events for total={total}, got {len(emitted)}'
    assert emitted[0] == 1, 'first completion must always be reported'
    assert emitted[-1] == total, 'terminal completion must always be reported'


def test_progress_emissions_are_serialized_so_a_stale_write_cannot_drop_the_terminal_event():
    """Status emissions run one after another, so the terminal `done: True`
    event cannot be lost. Saving a status reads the chat row, appends to
    `statusHistory` and writes the row back without a lock, so two overlapping
    saves drop one entry; if that entry is the terminal event, the loading
    animation in `StatusItem.svelte` never stops, even after a reload.
    """
    import open_webui.utils.middleware as M

    row = []  # stands in for the chat row's `statusHistory`

    async def emitter(event):
        snapshot = list(row)  # read
        # Earlier events are slower, so a later one overtakes them unless the
        # emissions are serialized.
        await asyncio.sleep(0.02 * (5 - event['data']['count']))
        row[:] = snapshot + [event]  # modify and write back

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        for done in range(1, 6):
            on_progress(done, 5)  # total=5 -> every completion passes the throttle
        while M._pii_progress_tasks:
            await asyncio.sleep(0.01)

    asyncio.run(drive())

    counts = [e['data']['count'] for e in row]
    assert counts == [1, 2, 3, 4, 5], f'lost or reordered status entries: {counts}'
    assert row[-1]['data']['done'] is True, 'the terminal event must survive as the last entry'


def test_a_failed_request_terminates_the_open_masking_status():
    """When masking fails, `finalize_on_failure` marks the open `pii_masking`
    status as done. Otherwise the last saved status stays at `done: false`, and
    the message keeps showing masking in progress under the error, even after a
    reload.
    """
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 40)  # the run then refuses at chunk 1 of 40
        while M._pii_progress_tasks:
            await asyncio.sleep(0)
        assert events[-1]['data']['done'] is False, 'precondition: the status is still open'
        await on_progress.finalize_on_failure()

    asyncio.run(drive())

    assert events[-1]['data']['done'] is True, 'the shimmer must stop on the failure path'
    assert events[-1]['data']['action'] == 'pii_masking'
    # The final event keeps the real count (1 of 40) rather than reporting
    # 40/40 for work that was never done.
    assert events[-1]['data']['count'] == 1
    assert events[-1]['data']['total'] == 40


def test_terminating_an_already_finished_masking_status_emits_nothing():
    """On the success path the terminal event has already gone out; appending a
    second one would put a duplicate status entry in the persisted history."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 2)
        on_progress(2, 2)
        while M._pii_progress_tasks:
            await asyncio.sleep(0)
        before = len(events)
        await on_progress.finalize_on_failure()
        return before

    before = asyncio.run(drive())

    assert len(events) == before, 'no extra status entry after a completed run'
    assert events[-1]['data']['done'] is True


def test_terminating_a_run_that_never_reported_progress_emits_nothing():
    """If `on_progress` was never called, as on the non-chunked path, a failure
    adds no `pii_masking` status, because the message never showed one."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        await on_progress.finalize_on_failure()

    asyncio.run(drive())

    assert events == []


def test_progress_on_a_small_total_still_emits_first_and_last_without_dividing_by_zero():
    """For a small `total` the throttle does not fail with a modulo-by-zero
    error and still reports the first and terminal events."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 3)
        on_progress(2, 3)
        on_progress(3, 3)
        # Wait until the task set is empty rather than for a fixed number of
        # loop turns: emissions are chained, so each one needs its own turn,
        # and two yields would return before the terminal event runs.
        while M._pii_progress_tasks:
            await asyncio.sleep(0)

    asyncio.run(drive())  # must not raise (no ZeroDivisionError)

    counts = [e['data']['count'] for e in events]
    assert counts[0] == 1
    assert counts[-1] == 3
    assert events[-1]['data']['done'] is True


def test_a_prompt_beyond_the_budget_is_refused_with_a_message_a_user_can_act_on():
    """A prompt longer than `max_maskable_chars()` is refused before any
    pipeline call, with a message telling the user to shorten it rather than
    naming an internal setting."""
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
    """A single message of exactly `max_maskable_chars()` characters, with no
    other history, is masked rather than refused."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    payload = _payload('x' * max_maskable_chars())
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(payload)  # must not raise
    assert seen, 'a within-budget paste must actually be masked, not refused'


def test_title_generation_task_is_exempt_from_chunking_even_when_oversized():
    """Title generation takes one whole-payload call and no size check, even
    when oversized. The pipeline skips NER for title, tag and follow-up
    generation and only re-masks from the vault by regex, which is fast at any
    size, so chunking or refusing these requests would only break large chats."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    payload = _payload('x' * (max_maskable_chars() + 1))
    payload['metadata']['task'] = 'title_generation'
    seen = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)):
        _run(payload)  # must not raise
    assert len(seen) == 1, 'an exempt task type must take exactly one POST, not be chunked'


def test_query_generation_task_still_chunks_when_oversized():
    """Query generation is still chunked when oversized. Its output goes to an
    external search service, so the pipeline runs full NER on it."""
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
    """Each chunk's `pii_detections_public` offsets are shifted by the chunk's
    position in the message and merged into the returned metadata. Without
    this, the PII card shows nothing for a message whose entities were found."""
    marker = 'SSN 123-45-6789'

    def _detections_for(text):
        idx = text.find(marker)
        return [{'type': 'US_SSN', 'start': idx, 'end': idx + len(marker)}] if idx != -1 else []

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
    """Detection entries from the pipeline that are not a dict, or whose
    start/end are not plain ints (a bool is rejected too), are dropped without
    failing an otherwise successful request."""

    def _detections_for(_text):
        return [
            'not-a-dict',
            {'type': 'US_SSN', 'start': True, 'end': 5},  # bool start
            {'type': 'US_SSN', 'start': 0, 'end': 'nope'},  # non-int end
            {'type': 'US_SSN', 'start': 0, 'end': 4},  # well-formed
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


def test_a_refused_request_cancels_its_remaining_chunk_calls():
    """When one chunk fails and the request is refused, the remaining chunk
    calls are cancelled. Otherwise they keep sending POSTs to the pipeline,
    writing vault rows for a prompt that will never be sent, and advancing the
    progress bar under the error.

    The mock records a request in `post()`, before its delay in `__aenter__`,
    so the test checks that no POST is recorded after the refusal. It waits
    inside the running loop instead of using `_run`, because `asyncio.run`
    cancels leftover tasks on return and would hide them.
    """
    sentinel = 'SENTINEL_ONLY_IN_ONE_CHUNK_c4b'
    content = sentinel + ' ' + BIG
    assert content.count(sentinel) == 1

    seen = []

    def _post(url, *, headers, json, ssl):
        body = json['body']
        text = body['messages'][0]['content']
        seen.append(text)
        if sentinel in text:
            raise aiohttp.ClientConnectionError('boom')
        out = {**body, 'messages': [{'role': 'user', 'content': text.upper()}]}
        resp = MagicMock()
        resp.json = AsyncMock(return_value=out)
        resp.raise_for_status = MagicMock()
        resp.content_type = 'application/json'

        async def _enter(_self=None):
            # Slow enough that siblings are mid-flight (or still queued behind
            # the semaphore) when the bad chunk refuses.
            await asyncio.sleep(0.05)
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

    async def drive():
        # One attempt, so the bad chunk refuses immediately without waiting for
        # retry backoff.
        with (
            patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=scm),
            patch('open_webui.routers.pipelines.PII_INLET_CHUNK_RETRIES', 1),
        ):
            with pytest.raises(PiiMaskingUnavailableError):
                await process_pipeline_inlet_filter(_request(), _payload(content), _user(), _models())
            at_refusal = len(seen)
            # 20 times the per-chunk delay: enough for any leftover call to
            # finish and for calls queued behind the semaphore to start.
            await asyncio.sleep(1.0)
            return at_refusal, len(seen)

    at_refusal, after_settling = asyncio.run(drive())

    total_jobs = len([t for t in seen if t]) # for the failure message
    assert at_refusal < len(split_text_for_pii(content)), (
        'the fixture must leave chunks unstarted at the moment of refusal, '
        f'otherwise this test cannot detect orphans (started={at_refusal})'
    )
    assert after_settling == at_refusal, (
        f'{after_settling - at_refusal} chunk POST(s) were issued AFTER the request was '
        f'already refused — siblings were not cancelled (total posts={total_jobs})'
    )


def test_one_bad_chunk_fails_the_whole_request_while_other_chunks_still_complete():
    """A single failing chunk refuses the whole request even though the other
    chunks succeed, so partially masked text is never returned. The
    all-chunks-fail test cannot catch this: with `return_exceptions=True` it
    would still raise, while this case would return partially masked text."""
    sentinel = 'SENTINEL_ONLY_IN_ONE_CHUNK_7f3'
    # Prepended so it sits inside the first chunk (about 1 800 characters), far
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
    """Mock inlet for multi-message payloads: returns every message uppercased,
    as the real pipeline returns the whole conversation, and records each
    POST's message contents.

    `_session` always returns a single message, so a multi-message skeleton
    would fail the message-count check before the assertion under test runs.
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
    """Return the chunk POSTs among the recorded calls. A chunk call carries
    exactly one non-blank message; the skeleton call carries the whole
    conversation, or a single blanked message for a one-message payload."""
    return [c for c in calls if len(c) == 1 and c[0]]


def _turn(*messages):
    return {
        'model': 'gpt-4',
        'messages': [{'role': r, 'content': c} for r, c in messages],
        'metadata': {'chat_id': 'c1'},
        'features': {'pii_masking': True},
    }


def test_only_the_message_being_sent_is_chunked_not_the_whole_history():
    """Oversized history is not chunked again on later turns, so the chunk
    count depends on the current message and not on conversation length.
    Older messages were masked and stored in the vault on the turn they were
    sent, and the pipeline re-masks them by regex; re-chunking them every turn
    would grow the work with each turn until the chat exceeds
    `PII_INLET_TOTAL_BUDGET_S` and refuses even a one-word message."""
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
    """An oversized older message is sent at full length in the skeleton call,
    not blanked and chunked, because the pipeline needs it to re-mask the
    history from the vault."""
    calls = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('user', BIG), ('assistant', 'Short reply.'), ('user', 'And a short follow-up.')))
    assert len(calls) == 1
    assert calls[0][0] == BIG, 'oversized history must be sent whole, not blanked'


def test_a_long_conversation_with_an_oversized_paste_is_not_refused_by_history_length():
    """Older history does not count against the masking time budget. The
    pipeline runs NER only on the last user and last assistant message (its
    `ner_indices`) and re-masks the rest with a fast vault regex, so charging
    history at the NER rate would refuse long chats."""
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
    """The last assistant message is chunked along with the message being sent,
    because the pipeline runs NER on it too; left whole, an oversized reply
    would exceed the skeleton call's 60-second socket read timeout. Earlier
    assistant replies are not chunked, so at most two messages are chunked."""
    calls = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('user', 'hi'), ('assistant', BIG)))
    assistant_only = len(_chunk_calls(calls))
    assert assistant_only > 1, 'an oversized assistant reply must be chunked'

    calls.clear()
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('assistant', BIG), ('user', BIG)))
    assert len(_chunk_calls(calls)) == 2 * assistant_only, 'both NER-priced messages are chunked, and only those two'

    # An earlier oversized assistant reply does not go through NER in the
    # pipeline, so it is not chunked.
    calls.clear()
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        _run(_turn(('assistant', BIG), ('user', 'ok'), ('assistant', 'Short reply.'), ('user', BIG)))
    assert len(_chunk_calls(calls)) == assistant_only, 'only the LAST assistant reply is chunked'


def test_the_two_ner_priced_messages_together_can_still_exceed_the_total_budget():
    """A long last assistant reply and a long paste both count against
    `PII_INLET_TOTAL_BUDGET_S`, and a request whose combined size exceeds it is
    refused before any POST."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    half = max_maskable_chars() // 2 + 1
    payload = _turn(('assistant', 'r' * half), ('user', 'b' * half))

    calls = []
    with patch('open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_history_session(calls)):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(payload)
    assert calls == [], 'refused requests must not touch the pipeline at all'


def test_a_detection_with_an_unhashable_type_is_dropped_rather_than_crashing_the_merge():
    """A detection whose `type` is unhashable is dropped. The merge
    de-duplicates on `(type, start, end)` in a set, so an unhashable type would
    raise after masking succeeded and turn a good response into a "masking
    unavailable" refusal."""

    def _detections_for(_text):
        return [
            {'type': ['US_SSN'], 'start': 0, 'end': 4},  # unhashable -> must be dropped
            {'type': 'US_SSN', 'start': 0, 'end': 4},  # well-formed
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


# ---------------------------------------------------------------------------
# Platform back-pressure (429 / transient 5xx) is not a refusal
#
# `_post_inlet_once` turns every non-2xx response into an HTTPException. A 400,
# 401, 403 or 422 is a deliberate refusal by the pipeline and is not retried. A
# 429 or transient 5xx means the platform has no free instance yet, so it is
# retried; otherwise one such response on any chunk would fail the whole run.
# ---------------------------------------------------------------------------


def _status_session(seen, *, status, fail_times):
    """Mock inlet that answers `status` for the first `fail_times` POSTs, then
    succeeds. As with a real response, `raise_for_status()` raises
    `aiohttp.ClientResponseError`, which `_post_inlet_once` converts into an
    HTTPException with that status."""
    calls = {'n': 0}

    def _post(url, *, headers, json, ssl):
        body = json['body']
        text = body['messages'][0]['content']
        seen.append(text)
        calls['n'] += 1
        failing = calls['n'] <= fail_times

        resp = MagicMock()
        resp.status = status
        resp.content_type = 'application/json'
        if failing:
            resp.json = AsyncMock(return_value={})

            def _raise():
                raise aiohttp.ClientResponseError(
                    request_info=SimpleNamespace(
                        real_url='http://pipeline/inlet',
                        method='POST',
                        url='http://pipeline/inlet',
                        headers={},
                    ),
                    history=(),
                    status=status,
                    message='Too Many Requests',
                )

            resp.raise_for_status = _raise
        else:
            out = {**body, 'messages': [{'role': 'user', 'content': text.upper()}]}
            resp.json = AsyncMock(return_value=out)
            resp.raise_for_status = MagicMock()

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


def test_a_429_from_the_platform_is_retried_rather_than_failing_the_request():
    """A 429 response is retried and the request succeeds. Failing on the first
    429 would discard every chunk that was already masked."""
    seen = []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession',
        return_value=_status_session(seen, status=429, fail_times=1),
    ), patch('open_webui.routers.pipelines.asyncio.sleep', AsyncMock()):
        out = _run(_payload(BIG))

    assert out['messages'][0]['content'] == BIG.upper()


def test_a_transient_5xx_is_retried_too():
    """A 503 response is retried like a 429, because Cloud Run returns 503
    while it is still starting an instance."""
    seen = []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession',
        return_value=_status_session(seen, status=503, fail_times=1),
    ), patch('open_webui.routers.pipelines.asyncio.sleep', AsyncMock()):
        out = _run(_payload(BIG))

    assert out['messages'][0]['content'] == BIG.upper()


def test_a_deliberate_refusal_is_still_not_retried():
    """A 400 response is not retried and reaches the caller as itself, not as a
    masking outage. The pipeline rejected the request on purpose, so a retry
    would only add load and return the same answer."""
    seen = []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession',
        return_value=_status_session(seen, status=400, fail_times=10_000),
    ), patch('open_webui.routers.pipelines.asyncio.sleep', AsyncMock()):
        with pytest.raises(Exception) as excinfo:
            _run(_payload(BIG))

    assert not isinstance(excinfo.value, PiiMaskingUnavailableError), (
        'a deliberate refusal must reach the user as itself, not as a masking outage'
    )


def test_a_429_that_never_clears_still_fails_closed():
    """Retries are limited: once the attempts are used up the request is
    refused, never forwarded unmasked."""
    seen = []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession',
        return_value=_status_session(seen, status=429, fail_times=10_000),
    ), patch('open_webui.routers.pipelines.asyncio.sleep', AsyncMock()):
        with pytest.raises(PiiMaskingUnavailableError):
            _run(_payload(BIG))


def test_the_bar_appears_before_the_first_chunk_finishes():
    """The first progress event is `0/N`, sent before the first POST, so the
    progress bar appears as soon as masking starts. The skeleton POST finishes
    before any chunk starts, so waiting for the first chunk would show only a
    spinner for a long time on a cold pipeline.
    """
    seen, progress = [], []
    with patch(
        'open_webui.routers.pipelines.aiohttp.ClientSession', return_value=_session(seen)
    ):
        _run(_payload(BIG), on_progress=lambda done, total: progress.append((done, total)))

    assert progress, 'a chunked prompt reported no progress at all'
    first_done, first_total = progress[0]
    assert first_done == 0, f'the first event was {progress[0]}, so the bar waited for a chunk'
    assert first_total > 1, 'the total must be the real chunk count, known up front'
    assert progress[-1] == (first_total, first_total), 'the terminal event must still close the bar'
