"""
Tests for PII outlet-restore on background task generators (tasks.py).

Background task completions are PII-masked on the inlet ([PERSON_1] goes to the
LLM). This module covers the outlet-restore side: _report_task_usage_and_restore
runs the pipeline outlet (which un-masks placeholders via the per-chat vault,
resolved by chat_id) and returns the restored assistant content, and
_apply_restored_content writes it back into the OpenAI-shaped response. Callers
fall back to the masked response when restore cannot be performed.

We mock process_pipeline_outlet_filter (the pipeline call) so no external
service is needed; the mock stands in for "the pipeline restored [PERSON_1] ->
real name".
"""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# stripe is an optional billing dependency not installed in the test environment.
sys.modules.setdefault("stripe", MagicMock())

from open_webui.routers import tasks
from open_webui.routers.tasks import (
    _report_task_usage_and_restore,
    _apply_restored_content,
    generate_title,
)


def _run(coro):
    return asyncio.run(coro)


def _user():
    return SimpleNamespace(
        id="user-1", email="u@example.com", name="U", role="user"
    )


def _request():
    return MagicMock()


def _masked_response(content, usage=None):
    """OpenAI-shaped completion with a masked assistant content."""
    resp = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        resp["usage"] = usage
    return resp


def _payload(chat_id="chat-1"):
    metadata = {"task": "title_generation"}
    if chat_id is not None:
        metadata["chat_id"] = chat_id
    return {
        "model": "task-model",
        "messages": [{"role": "user", "content": "masked prompt with [PERSON_1]"}],
        "metadata": metadata,
    }


def _outlet_returning(content):
    """AsyncMock outlet that echoes the request body but with the last message
    content replaced by `content` (simulates the pipeline's restore)."""
    async def _fake(request, body, user, models):
        body = {**body, "messages": list(body["messages"])}
        body["messages"][-1] = {**body["messages"][-1], "content": content}
        return body
    return AsyncMock(side_effect=_fake)


# ---------------------------------------------------------------------------
# _report_task_usage_and_restore — core restore logic
# ---------------------------------------------------------------------------

class TestReportTaskUsageAndRestore:

    def test_returns_real_name_when_outlet_restores(self):
        outlet = _outlet_returning("Alice Smith")
        with patch.object(tasks, "process_pipeline_outlet_filter", outlet):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("[PERSON_1]", usage={"total_tokens": 10}),
                {},
            ))
        assert restored == "Alice Smith"
        assert outlet.await_count == 1

    def test_noop_when_nothing_to_restore(self):
        # Masking OFF (or no placeholders): the pipeline returns content unchanged.
        outlet = _outlet_returning("Just a normal title")
        with patch.object(tasks, "process_pipeline_outlet_filter", outlet):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("Just a normal title", usage={"total_tokens": 5}),
                {},
            ))
        # Restored equals the original — applying it is a no-op.
        assert restored == "Just a normal title"

    def test_missing_chat_id_returns_none_and_skips_outlet(self):
        outlet = _outlet_returning("Alice Smith")
        with patch.object(tasks, "process_pipeline_outlet_filter", outlet):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(chat_id=None),
                _masked_response("[PERSON_1]", usage={"total_tokens": 10}),
                {},
            ))
        assert restored is None
        # No chat_id → no vault to resolve → outlet never called.
        assert outlet.await_count == 0

    def test_non_dict_response_returns_none(self):
        # Streaming responses (e.g. moa stream=True) are not dict-shaped.
        outlet = _outlet_returning("Alice Smith")
        streaming_like = SimpleNamespace(status_code=200)  # not a dict
        with patch.object(tasks, "process_pipeline_outlet_filter", outlet):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(), _payload(), streaming_like, {},
            ))
        assert restored is None
        assert outlet.await_count == 0

    def test_outlet_failure_returns_none(self):
        failing = AsyncMock(side_effect=RuntimeError("pipeline down"))
        with patch.object(tasks, "process_pipeline_outlet_filter", failing):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("[PERSON_1]", usage={"total_tokens": 10}),
                {},
            ))
        # Best-effort: swallow the error, signal fallback with None.
        assert restored is None

    def test_malformed_outlet_result_returns_none(self):
        # Outlet returns something without a usable messages list.
        weird = AsyncMock(return_value={"unexpected": True})
        with patch.object(tasks, "process_pipeline_outlet_filter", weird):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("[PERSON_1]", usage={"total_tokens": 10}),
                {},
            ))
        assert restored is None

    def test_usage_attached_to_outlet_message(self):
        # Langfuse usage side-effect preserved: usage rides on the outlet message.
        captured = {}

        async def _fake(request, body, user, models):
            captured["body"] = body
            return {**body, "messages": [{**body["messages"][-1], "content": "Alice Smith"}]}

        with patch.object(tasks, "process_pipeline_outlet_filter", AsyncMock(side_effect=_fake)):
            _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("[PERSON_1]", usage={"total_tokens": 42}),
                {},
            ))
        assert captured["body"]["messages"][-1].get("usage") == {"total_tokens": 42}
        assert captured["body"]["chat_id"] == "chat-1"

    def test_restore_runs_even_without_usage(self):
        # No usage present, but chat_id is and the content is masked → restore
        # must still run.
        outlet = _outlet_returning("Alice Smith")
        with patch.object(tasks, "process_pipeline_outlet_filter", outlet):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("[PERSON_1]"),  # no usage, but masked
                {},
            ))
        assert restored == "Alice Smith"
        assert outlet.await_count == 1

    def test_skips_outlet_when_no_usage_and_no_placeholder(self):
        # Nothing to do: no usage to report AND no placeholder to restore
        # (masking off / nothing masked). Skip the extra outbound outlet call.
        outlet = _outlet_returning("Alice Smith")
        with patch.object(tasks, "process_pipeline_outlet_filter", outlet):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("Just a normal title"),  # no usage, no placeholder
                {},
            ))
        assert restored is None
        assert outlet.await_count == 0

    def test_outlet_timeout_falls_back_to_none(self):
        # A stuck/slow pipeline outlet must not hang the chat: the bounded call
        # times out and we fall back to masked content (None) instead of blocking.
        async def _hang(request, body, user, models):
            await asyncio.sleep(1)  # longer than the patched timeout
            return body

        with patch.object(tasks, "process_pipeline_outlet_filter", AsyncMock(side_effect=_hang)), \
             patch.object(tasks, "PII_TASK_OUTLET_RESTORE_TIMEOUT", 0.05):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("[PERSON_1]", usage={"total_tokens": 10}),
                {},
            ))
        assert restored is None  # timed out → masked fallback, no raise

    def test_outlet_cancellation_is_swallowed(self):
        # CancelledError (BaseException) from the outlet must NOT propagate up to
        # crash the chat response handler — it is caught and we fall back.
        cancelling = AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(tasks, "process_pipeline_outlet_filter", cancelling):
            restored = _run(_report_task_usage_and_restore(
                _request(), _user(),
                _payload(),
                _masked_response("[PERSON_1]", usage={"total_tokens": 10}),
                {},
            ))
        assert restored is None  # cancellation swallowed, not propagated


# ---------------------------------------------------------------------------
# _apply_restored_content — write-back into the response
# ---------------------------------------------------------------------------

class TestApplyRestoredContent:

    def test_overwrites_content(self):
        resp = _masked_response("[PERSON_1]")
        out = _apply_restored_content(resp, "Alice Smith")
        assert out["choices"][0]["message"]["content"] == "Alice Smith"

    def test_none_is_noop(self):
        resp = _masked_response("[PERSON_1]")
        out = _apply_restored_content(resp, None)
        assert out["choices"][0]["message"]["content"] == "[PERSON_1]"

    def test_non_dict_response_is_noop(self):
        streaming_like = SimpleNamespace(status_code=200)
        out = _apply_restored_content(streaming_like, "Alice Smith")
        assert out is streaming_like

    def test_malformed_response_does_not_raise(self):
        resp = {"no_choices": True}
        out = _apply_restored_content(resp, "Alice Smith")
        # Left untouched, no exception.
        assert out == {"no_choices": True}


# ---------------------------------------------------------------------------
# generate_title endpoint — proves the value middleware PERSISTS is restored
# ---------------------------------------------------------------------------
#
# middleware.py does: res = await generate_title(...); title = json.loads(
# res["choices"][0]["message"]["content"])["title"]; Chats.update_chat_title_by_id(
# chat_id, title). So asserting generate_title's returned content is restored
# proves the persisted DB title (and chat:title socket event) carry the real name.

def _title_request(enable=True):
    request = MagicMock()
    request.app.state.config.ENABLE_TITLE_GENERATION = enable
    request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE = ""
    request.app.state.config.TASK_MODEL = None
    request.app.state.config.TASK_MODEL_EXTERNAL = None
    request.app.state.MODELS = {"gpt-4": {"id": "gpt-4"}}
    # request.state has no `direct` / `metadata` attributes set.
    request.state = SimpleNamespace()
    return request


def _title_form():
    return {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hi, I'm [PERSON_1]"}],
        "chat_id": "chat-1",
    }


def _patch_title_pipeline(outlet):
    """Patch the pipeline seams used by generate_title. inlet returns payload
    unchanged; generate_chat_completion returns a masked JSON title; outlet is
    the caller-supplied AsyncMock."""
    async def _inlet(request, payload, user, models):
        return payload

    async def _gen(request, form_data, user):
        return {
            "choices": [
                {"message": {"role": "assistant",
                             "content": json.dumps({"title": "Chat with [PERSON_1]"})}}
            ],
            "usage": {"total_tokens": 20},
        }

    return [
        patch.object(tasks, "process_pipeline_inlet_filter", AsyncMock(side_effect=_inlet)),
        patch.object(tasks, "generate_chat_completion", AsyncMock(side_effect=_gen)),
        patch.object(tasks, "get_task_model_id", MagicMock(return_value="gpt-4")),
        patch.object(tasks, "title_generation_template", AsyncMock(return_value="prompt")),
        patch.object(tasks, "process_pipeline_outlet_filter", outlet),
    ]


def _apply_patches(patches, fn):
    if not patches:
        return fn()
    with patches[0]:
        return _apply_patches(patches[1:], fn)


class TestGenerateTitlePersistedValue:

    def test_returned_title_is_restored(self):
        outlet = _outlet_returning(json.dumps({"title": "Chat with Alice Smith"}))
        patches = _patch_title_pipeline(outlet)
        res = _apply_patches(patches, lambda: _run(
            generate_title(_title_request(), _title_form(), _user())
        ))
        content = res["choices"][0]["message"]["content"]
        # This is exactly what middleware json.loads(...)['title'] persists.
        assert json.loads(content)["title"] == "Chat with Alice Smith"

    def test_masking_off_leaves_title_unchanged(self):
        # Outlet returns content unchanged (no placeholders / masking OFF).
        original = json.dumps({"title": "Chat with [PERSON_1]"})
        outlet = _outlet_returning(original)
        patches = _patch_title_pipeline(outlet)
        res = _apply_patches(patches, lambda: _run(
            generate_title(_title_request(), _title_form(), _user())
        ))
        content = res["choices"][0]["message"]["content"]
        assert content == original

    def test_outlet_failure_falls_back_to_masked(self):
        outlet = AsyncMock(side_effect=RuntimeError("pipeline down"))
        patches = _patch_title_pipeline(outlet)
        res = _apply_patches(patches, lambda: _run(
            generate_title(_title_request(), _title_form(), _user())
        ))
        content = res["choices"][0]["message"]["content"]
        # No crash; masked value returned (title generation must not fail).
        assert json.loads(content)["title"] == "Chat with [PERSON_1]"

    def test_hanging_outlet_does_not_hang_or_crash_title(self):
        # End-to-end: a pipeline outlet that hangs past the bound must not block
        # or crash title generation — the chat survives with the masked title.
        async def _hang(request, body, user, models):
            await asyncio.sleep(1)  # longer than the patched bound
            return body

        outlet = AsyncMock(side_effect=_hang)
        patches = _patch_title_pipeline(outlet)
        with patch.object(tasks, "PII_TASK_OUTLET_RESTORE_TIMEOUT", 0.05):
            res = _apply_patches(patches, lambda: _run(
                generate_title(_title_request(), _title_form(), _user())
            ))
        content = res["choices"][0]["message"]["content"]
        # Outlet hung → bounded out → masked title returned, no crash.
        assert json.loads(content)["title"] == "Chat with [PERSON_1]"
