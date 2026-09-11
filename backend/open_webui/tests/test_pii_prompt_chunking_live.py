"""Live end-to-end check for prompt-path PII chunking.

Every other PII chunking test mocks the external PII pipeline. This one
does not: it sends a genuinely oversized prompt through
`process_pipeline_inlet_filter` against the REAL staging pipeline and asserts
the PII comes back masked. Without chunking, such a prompt raised
`PiiMaskingUnavailableError` because one request could not finish inside the
60 s socket-read timeout.

Opt-in ONLY: skipped unless `KEEPER_PII_LIVE=1` is set, so the default suite
and CI never reach the network. The connection (URL + key) is read from the
local `backend/data/webui.db` at runtime — never hardcoded, never committed.
If no matching staging-pipeline connection exists in that database, the test
skips rather than failing.
"""

import asyncio
import json
import os
import sqlite3
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('stripe', MagicMock())

from open_webui.routers.pipelines import process_pipeline_inlet_filter  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get('KEEPER_PII_LIVE') != '1',
    reason='live PII pipeline check is opt-in; set KEEPER_PII_LIVE=1 to run it',
)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'webui.db')


def _connection():
    db_path = os.path.abspath(DB_PATH)
    try:
        # Read-only, URI-mode connect: a missing file raises instead of being
        # silently CREATED (the default `sqlite3.connect` rwc mode would
        # otherwise leave a stray, table-less .db file behind on a machine
        # that has never run the app).
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        row = conn.execute('SELECT data FROM config ORDER BY id DESC LIMIT 1').fetchone()
    except sqlite3.Error:
        pytest.skip(f'no local database at {db_path}')
    if row is None:
        pytest.skip('no staging pipeline connection in the local database')
    openai = json.loads(row[0]).get('openai', {})
    for idx, (url, key) in enumerate(zip(openai.get('api_base_urls', []), openai.get('api_keys', []))):
        if 'pipelines-v4--staging' in url:
            return idx, url.rstrip('/'), key
    pytest.skip('no staging pipeline connection in the local database')


def _request_and_models():
    idx, url, key = _connection()

    base_urls = [''] * (idx + 1)
    api_keys = [''] * (idx + 1)
    base_urls[idx] = url
    api_keys[idx] = key

    models = {
        'gpt-4': {'id': 'gpt-4'},
        'pii_filter_pipeline': {
            'id': 'pii_filter_pipeline',
            'urlIdx': idx,
            'pipeline': {'type': 'filter', 'priority': 0, 'pipelines': ['*']},
        },
    }

    request = MagicMock()
    request.app.state.config.OPENAI_API_BASE_URLS = base_urls
    request.app.state.config.OPENAI_API_KEYS = api_keys
    request.app.state.config.USER_PERMISSIONS = {'chat': {'pii_masking_enforced': False}}
    request.state = SimpleNamespace()
    return request, models


def _user():
    return SimpleNamespace(id='u1', email='t@e.com', name='T', role='user', settings=None)


def _payload(content, chat_id):
    return {
        'model': 'gpt-4',
        'messages': [{'role': 'user', 'content': content}],
        'metadata': {'chat_id': chat_id},
        'features': {'pii_masking': True},
    }


def test_live_oversized_prompt_is_masked_end_to_end():
    """A prompt far past the single-call
    budget must come back masked instead of raising PiiMaskingUnavailableError.
    """
    content = (
        'Ivan Horvat, OIB 12345678903, IBAN HR1210010051863000160. Ugovor o poslovnoj suradnji i uvjetima isporuke. '
    ) * 300
    assert len(content) > 25_000

    request, models = _request_and_models()
    chat_id = f'live-test-{uuid.uuid4()}'

    out = asyncio.run(process_pipeline_inlet_filter(request, _payload(content, chat_id), _user(), models))
    masked = out['messages'][0]['content']
    assert '12345678903' not in masked, 'OIB survived masking — LEAK'
    assert 'HR1210010051863000160' not in masked, 'IBAN survived masking — LEAK'
    assert '[OIB_' in masked or '[HR_OIB_' in masked, 'no OIB placeholder in masked output — masking did not run'


def test_live_prompt_at_the_reported_size_is_masked_inside_the_raised_budget():
    """The size the user actually hit: ~150 000 characters, which the old
    120 s budget refused outright. Asserts it now completes, and reports the
    real wall-clock so `PII_INLET_CHARS_PER_SECOND` /
    `PII_INLET_EFFECTIVE_SPEEDUP` can be re-derived at this size rather than
    extrapolated from the ~33 000-character case above.

    The per-repetition counter keeps the text non-periodic, so chunk
    boundaries fall in different places than they would in a pure repeat.
    """
    import time

    from open_webui.utils.pii_chunking import (
        PII_INLET_TOTAL_BUDGET_S,
        estimated_masking_seconds,
        max_maskable_chars,
    )

    content = ''.join(
        f'Ivan Horvat {i}, OIB 12345678903, IBAN HR1210010051863000160. Ugovor broj {i} o poslovnoj suradnji. '
        for i in range(1600)
    )
    assert len(content) > 150_000, len(content)
    assert len(content) <= max_maskable_chars(), (
        f'{len(content)} chars is past the {max_maskable_chars()}-char cap; this test would only prove the guard'
    )

    request, models = _request_and_models()
    chat_id = f'live-test-{uuid.uuid4()}'

    started = time.monotonic()
    out = asyncio.run(process_pipeline_inlet_filter(request, _payload(content, chat_id), _user(), models))
    elapsed = time.monotonic() - started

    masked = out['messages'][0]['content']
    print(
        f'\n[live] {len(content)} chars masked in {elapsed:.1f}s '
        f'(estimated {estimated_masking_seconds(0, len(content)):.0f}s, budget {PII_INLET_TOTAL_BUDGET_S}s) '
        f'-> {len(content) / elapsed:.0f} chars/s effective'
    )

    assert '12345678903' not in masked, 'OIB survived masking — LEAK'
    assert 'HR1210010051863000160' not in masked, 'IBAN survived masking — LEAK'
    assert '[OIB_' in masked or '[HR_OIB_' in masked, 'no OIB placeholder in masked output — masking did not run'
    assert elapsed < PII_INLET_TOTAL_BUDGET_S, 'completed, but only by exceeding the budget it was allowed'
