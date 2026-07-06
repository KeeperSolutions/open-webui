"""
Tests for the LLM transient-error retry on the OpenAI-compatible chat-completion
path (routers/openai.py::generate_chat_completion, TRAU-520).

The retry loop wraps the upstream `session.request(...)` call. It retries on a
transient connection/timeout exception or a retryable HTTP status (429/5xx), and
propagates every other status (notably non-retryable 4xx like a 400
context_length_exceeded) immediately. Pipeline models are not retried, and a
total wall-clock budget caps the whole loop.

We call generate_chat_completion() directly (bypassing FastAPI's Depends) with a
minimal request stub, patch the module's heavy preamble helpers to no-ops, and
mock aiohttp.ClientSession so session.request() returns a scripted sequence of
responses/exceptions. asyncio.sleep is patched out so backoff is instant, and
time.monotonic is patched where a test needs to drive the budget guard.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

# stripe is an optional billing dependency not installed in the test environment.
# Mock it before any open_webui import triggers the import chain (billing router
# is imported transitively by routers/openai.py).
sys.modules.setdefault("stripe", MagicMock())

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

import open_webui.routers.openai as openai_router
from open_webui.routers.openai import generate_chat_completion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(base_url="https://api.openai.com/v1", key="sk-test"):
    """Minimal FastAPI request stub with just the state the function reads."""
    request = MagicMock()
    request.app.state.OPENAI_MODELS = {}  # set per test via _make_model
    request.app.state.config.OPENAI_API_CONFIGS = {}  # real dict -> api_config = {}
    request.app.state.config.OPENAI_API_BASE_URLS = [base_url]
    request.app.state.config.OPENAI_API_KEYS = [key]
    return request


def _make_user():
    user = MagicMock()
    user.id = "user-1"
    user.role = "admin"
    user.name = "Test"
    user.email = "test@example.com"
    return user


def _make_model(pipeline=False):
    """An OPENAI_MODELS entry. pipeline=True marks it as a Pipelines model."""
    model = {"id": "gpt-4", "urlIdx": 0, "name": "gpt-4"}
    if pipeline:
        model["pipeline"] = {"type": "pipe"}
    return model


def _fake_clock(values):
    """
    Isolated monotonic clock: returns the scripted values in order, then repeats
    the last one forever (never raises StopIteration). Patched onto the openai
    module's `time` NAME only, so asyncio's own time.monotonic() is untouched.
    """
    seq = list(values)
    state = {"i": 0}

    def _mono():
        v = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        return v

    return SimpleNamespace(monotonic=_mono)


def _resp(status, content_type="application/json", body=None):
    """A mock aiohttp ClientResponse."""
    r = MagicMock()
    r.status = status
    r.headers = {"Content-Type": content_type}
    r.json = AsyncMock(return_value=body if body is not None else {"ok": True})
    r.text = AsyncMock(return_value="upstream error body")
    r.close = MagicMock()  # cleanup_response calls response.close() (sync)
    return r


class _SessionPatch:
    """
    Patch aiohttp.ClientSession in the openai module so each ClientSession(...)
    yields a fresh session whose .request is a shared AsyncMock scripted with
    `outcomes` (each item: a mock response to return, or an Exception to raise).
    Every session.close() is an awaitable no-op. Exposes .call_count.
    """

    def __init__(self, outcomes):
        self.request = AsyncMock(side_effect=outcomes)
        self.sessions = []

    def _make_session(self, *args, **kwargs):
        s = MagicMock()
        s.request = self.request
        s.close = AsyncMock()
        self.sessions.append(s)
        return s

    def __enter__(self):
        self._patch = patch.object(
            openai_router.aiohttp, "ClientSession", side_effect=self._make_session
        )
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False

    @property
    def call_count(self):
        return self.request.call_count


def _run(request, model, outcomes, form_data=None):
    """
    Drive generate_chat_completion with the heavy preamble patched out and a
    scripted session. Returns (result_or_raise, session_patch). asyncio.sleep is
    neutralised so backoff does not slow the test.
    """
    request.app.state.OPENAI_MODELS = {"gpt-4": model}
    form_data = form_data or {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}

    with _SessionPatch(outcomes) as sess, \
        patch.object(openai_router, "get_all_models", AsyncMock(return_value={})), \
        patch.object(openai_router, "get_headers_and_cookies", AsyncMock(return_value=({}, {}))), \
        patch.object(openai_router.Models, "get_model_by_id", MagicMock(return_value=None)), \
        patch.object(openai_router.asyncio, "sleep", AsyncMock()):
        coro = generate_chat_completion(
            request, form_data, user=_make_user(), bypass_filter=True, db=None
        )
        result = asyncio.run(coro)
    return result, sess


# ---------------------------------------------------------------------------
# 1. Retryable status recovers
# ---------------------------------------------------------------------------

def test_retryable_status_then_success_returns_body():
    request = _make_request()
    body = {"choices": [{"message": {"content": "ok"}}]}
    result, sess = _run(request, _make_model(), [_resp(503), _resp(200, body=body)])
    assert result == body
    assert sess.call_count == 2  # one retry


def test_retryable_backoff_sleeps_between_attempts():
    request = _make_request()
    with patch.object(openai_router.asyncio, "sleep", AsyncMock()) as sleep_mock:
        # re-run inside our own sleep patch so we can assert on it
        request.app.state.OPENAI_MODELS = {"gpt-4": _make_model()}
        fd = {"model": "gpt-4", "messages": []}
        with _SessionPatch([_resp(503), _resp(200)]), \
            patch.object(openai_router, "get_all_models", AsyncMock(return_value={})), \
            patch.object(openai_router, "get_headers_and_cookies", AsyncMock(return_value=({}, {}))), \
            patch.object(openai_router.Models, "get_model_by_id", MagicMock(return_value=None)):
            asyncio.run(generate_chat_completion(request, fd, user=_make_user(), bypass_filter=True, db=None))
        assert sleep_mock.await_count == 1  # backoff before the single retry


# ---------------------------------------------------------------------------
# 2. Non-retryable status is NOT retried
# ---------------------------------------------------------------------------

def test_non_retryable_400_returns_immediately():
    request = _make_request()
    err = {"error": {"code": "context_length_exceeded", "message": "too long"}}
    result, sess = _run(request, _make_model(), [_resp(400, body=err)])
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    assert sess.call_count == 1  # no retry on a non-retryable status


def test_non_retryable_does_not_sleep():
    request = _make_request()
    request.app.state.OPENAI_MODELS = {"gpt-4": _make_model()}
    fd = {"model": "gpt-4", "messages": []}
    with _SessionPatch([_resp(401, body={"error": "bad key"})]), \
        patch.object(openai_router, "get_all_models", AsyncMock(return_value={})), \
        patch.object(openai_router, "get_headers_and_cookies", AsyncMock(return_value=({}, {}))), \
        patch.object(openai_router.Models, "get_model_by_id", MagicMock(return_value=None)), \
        patch.object(openai_router.asyncio, "sleep", AsyncMock()) as sleep_mock:
        result = asyncio.run(
            generate_chat_completion(request, fd, user=_make_user(), bypass_filter=True, db=None)
        )
    assert isinstance(result, JSONResponse)
    assert result.status_code == 401
    assert sleep_mock.await_count == 0


# ---------------------------------------------------------------------------
# 3. Exhaustion: retryable status on every attempt -> returns last error as-is
# ---------------------------------------------------------------------------

def test_all_attempts_retryable_returns_upstream_error():
    request = _make_request()
    # Constant clock keeps us under the budget; all 3 attempts run.
    with patch.object(openai_router, "time", _fake_clock([0.0])):
        result, sess = _run(
            request, _make_model(),
            [_resp(503, body={"error": "busy"}),
             _resp(503, body={"error": "busy"}),
             _resp(503, body={"error": "busy"})],
        )
    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    assert sess.call_count == openai_router.LLM_RETRY_MAX  # exactly 3 attempts


# ---------------------------------------------------------------------------
# 4. Exception (connection/timeout) is retried, then succeeds
# ---------------------------------------------------------------------------

def test_connection_exception_then_success():
    request = _make_request()
    body = {"choices": []}
    result, sess = _run(
        request, _make_model(),
        [aiohttp.ClientError("boom"), asyncio.TimeoutError(), _resp(200, body=body)],
    )
    assert result == body
    assert sess.call_count == 3


def test_exception_on_every_attempt_raises_terminal_502_or_500():
    request = _make_request()
    with patch.object(openai_router, "time", _fake_clock([0.0])):
        with pytest.raises(HTTPException) as ei:
            _run(
                request, _make_model(),
                [aiohttp.ClientError("a"), aiohttp.ClientError("b"), aiohttp.ClientError("c")],
            )
    # Terminal handler: r is None after cleanup -> 500 "Server Connection Error".
    assert ei.value.status_code == 500


# ---------------------------------------------------------------------------
# 5. Pipeline models are NOT retried (single attempt)
# ---------------------------------------------------------------------------

def test_pipeline_model_not_retried():
    request = _make_request()
    result, sess = _run(request, _make_model(pipeline=True), [_resp(503, body={"error": "busy"})])
    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    assert sess.call_count == 1  # pipeline -> max_attempts == 1


# ---------------------------------------------------------------------------
# 6. Total wall-clock budget stops a new attempt
# ---------------------------------------------------------------------------

def test_budget_exhaustion_stops_before_next_attempt():
    request = _make_request()
    request.app.state.OPENAI_MODELS = {"gpt-4": _make_model()}
    fd = {"model": "gpt-4", "messages": []}
    # monotonic sequence (isolated to the openai module):
    #   call 1 -> retry_deadline = 0 + 90 = 90
    #   call 2 -> status-retry budget check (attempt 0): 1 < 90 -> retry
    #   call 3 -> top-of-loop guard (attempt 1): 100 >= 90 -> break -> terminal 503
    with _SessionPatch([_resp(503), _resp(503), _resp(503)]) as sess, \
        patch.object(openai_router, "get_all_models", AsyncMock(return_value={})), \
        patch.object(openai_router, "get_headers_and_cookies", AsyncMock(return_value=({}, {}))), \
        patch.object(openai_router.Models, "get_model_by_id", MagicMock(return_value=None)), \
        patch.object(openai_router.asyncio, "sleep", AsyncMock()), \
        patch.object(openai_router, "time", _fake_clock([0.0, 1.0, 100.0])):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(
                generate_chat_completion(request, fd, user=_make_user(), bypass_filter=True, db=None)
            )
    assert ei.value.status_code == 503
    # Budget blocked the 2nd network attempt: only one request went out.
    assert sess.call_count == 1


# ---------------------------------------------------------------------------
# 7. Streaming success returns a StreamingResponse and is never retried
# ---------------------------------------------------------------------------

def test_streaming_success_returns_streamingresponse():
    request = _make_request()
    result, sess = _run(
        request, _make_model(), [_resp(200, content_type="text/event-stream")]
    )
    assert isinstance(result, StreamingResponse)
    assert sess.call_count == 1
