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
