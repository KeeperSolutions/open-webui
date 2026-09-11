"""The initial title task must be built from the POST-masking payload.

0.11 introduced a fire-and-forget initial title generation that ran before two
things the PII guarantee depends on:

  * `request.state.metadata` — which is what carries `features.pii_masking` into
    a task payload (TRAU-522's propagation), and
  * the chat inlet — which is what puts the turn's PII into the thread vault.

Without the vault the pipeline has no deterministic re-mask to apply, so the
task payload's protection collapses to NER recall over a mostly-template prompt,
and that misses names it catches in a bare sentence — measured: the real title
template leaks "Robert Plant" while masking "John Cena".

`start_initial_title_generation` is the seam that keeps the task's context built
from whatever the caller hands it, so the caller can hand it the masked payload.

What these tests do NOT cover: the toggle itself never travels through the ctx
built here — `generate_title` reads it from `request.state.metadata`
(`routers/tasks.py`), and the inlet side of that is TRAU-522's tests in
`test_pii_toggle.py`. That `request.state.metadata` is populated by the time the
title starts is a property of the call ORDER in `main.py`'s `chat_completion`,
which has no importable seam and is not unit-tested here.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# stripe is an optional billing dependency not installed in the test environment.
# `open_webui.utils.middleware` reaches it through routers.billing, so mock it
# before that import.
sys.modules.setdefault('stripe', MagicMock())

from open_webui.constants import TASKS
from open_webui.utils.middleware import start_initial_title_generation


def _metadata():
    return {
        'chat_id': 'chat-1',
        'session_id': 's-1',
        'features': {'pii_masking': True},
    }


async def _run(form_data=None, metadata=None, handler=None):
    """Call the helper with the handler patched, and let its task finish."""
    handler = handler or AsyncMock()
    with patch('open_webui.utils.middleware.background_tasks_handler', handler), patch(
        'open_webui.utils.middleware.get_event_emitter', AsyncMock(return_value='emitter')
    ):
        await start_initial_title_generation(
            request='req',
            form_data=form_data if form_data is not None else {'messages': []},
            user='user',
            metadata=metadata if metadata is not None else _metadata(),
            message_id='assistant-1',
            title_task=True,
        )
        # The helper schedules the work; give the loop a turn to run it.
        for _ in range(3):
            await asyncio.sleep(0)
    return handler


@pytest.mark.asyncio
async def test_ctx_carries_the_payload_it_was_given():
    """The masked payload the caller passes must be the one the task sees.

    Guards against the helper reaching back for a pre-inlet payload.
    """
    masked = {'messages': [{'role': 'user', 'content': 'tko je [PERSON_1]?'}]}

    handler = await _run(form_data=masked)

    ctx = handler.call_args[0][0]
    assert ctx['form_data'] is masked
    assert ctx['metadata']['message_id'] == 'assistant-1'
    assert ctx['tasks'] == {TASKS.TITLE_GENERATION: True}


@pytest.mark.asyncio
async def test_title_ctx_keeps_the_callers_metadata():
    """Apart from the one key it deliberately drops, the ctx carries the
    caller's metadata unchanged — `background_tasks_handler` needs `chat_id` to
    load the conversation and the event emitter needs the rest."""
    handler = await _run()

    ctx = handler.call_args[0][0]
    assert ctx['metadata'] == {**_metadata(), 'message_id': 'assistant-1'}


@pytest.mark.asyncio
async def test_title_ctx_does_not_replay_the_pii_card_bridge():
    """`background_tasks_handler` emits `chat:message:pii` and upserts the card
    for ANY ctx whose metadata carries `pii_detections_public`, before it runs a
    single task. The post-inlet metadata this helper now receives has that key,
    and the normal post-completion handler emits the same card again — so
    leaving it in would fire the bridge twice per masked turn.
    """
    metadata = {**_metadata(), 'pii_detections_public': [{'type': 'PERSON', 'start': 7, 'end': 19}]}

    handler = await _run(metadata=metadata)

    ctx = handler.call_args[0][0]
    assert ctx['metadata'] == {**_metadata(), 'message_id': 'assistant-1'}
    # The caller's metadata is shared with the chat turn — never mutate it.
    assert 'pii_detections_public' in metadata


@pytest.mark.asyncio
async def test_a_failing_title_task_never_breaks_the_chat():
    """Title generation is best-effort; the chat turn must not be affected."""
    handler = AsyncMock(side_effect=RuntimeError('task model exploded'))

    await _run(handler=handler)  # must not raise

    assert handler.await_count == 1
