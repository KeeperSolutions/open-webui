"""Live end-to-end tests for PII chunking on the prompt path.

The other PII chunking tests mock the external PII pipeline. These tests send
oversized prompts through `process_pipeline_inlet_filter` to the real staging
pipeline and check that the PII comes back masked. Without chunking, such a
prompt raises `PiiMaskingUnavailableError`, because a single request cannot
finish within the 60-second socket-read timeout.

The tests run only when `KEEPER_PII_LIVE=1` is set, so the default suite and
CI never use the network. The pipeline URL and key are read at runtime from
the local `backend/data/webui.db` and are never stored in the repository. If
that database has no staging pipeline connection, the tests are skipped.
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
        # Open read-only in URI mode so a missing file raises an error. The
        # default `sqlite3.connect` mode creates the file, which would leave an
        # empty .db file behind on a machine that has never run the app.
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
    """A prompt too large for one masking request comes back masked instead of
    raising `PiiMaskingUnavailableError`.
    """
    content = (
        'John Doe, SSN 123-45-6789, IBAN GB82WEST12345698765432. Service agreement and delivery terms. '
    ) * 300
    assert len(content) > 25_000

    request, models = _request_and_models()
    chat_id = f'live-test-{uuid.uuid4()}'

    out = asyncio.run(process_pipeline_inlet_filter(request, _payload(content, chat_id), _user(), models))
    masked = out['messages'][0]['content']
    assert '123-45-6789' not in masked, 'SSN survived masking — LEAK'
    assert 'GB82WEST12345698765432' not in masked, 'IBAN survived masking — LEAK'
    assert '[US_SSN_' in masked, 'no SSN placeholder in masked output — masking did not run'


def test_live_prompt_at_the_reported_size_is_masked_inside_the_raised_budget():
    """A prompt of about 150 000 characters is masked within
    `PII_INLET_TOTAL_BUDGET_S`.

    The test prints the elapsed time and the effective characters per second,
    so `PII_INLET_CHARS_PER_SECOND` and `PII_INLET_EFFECTIVE_SPEEDUP` can be
    checked at this size instead of extrapolated from the ~33 000-character
    prompt in the previous test.

    Each sentence contains a counter, so the text does not repeat exactly and
    chunk boundaries do not all fall at the same place in the sentence.
    """
    import time

    from open_webui.utils.pii_chunking import (
        PII_INLET_TOTAL_BUDGET_S,
        estimated_masking_seconds,
        max_maskable_chars,
    )

    content = ''.join(
        f'John Doe {i}, SSN 123-45-6789, IBAN GB82WEST12345698765432. Contract {i} for business cooperation. '
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

    assert '123-45-6789' not in masked, 'SSN survived masking — LEAK'
    assert 'GB82WEST12345698765432' not in masked, 'IBAN survived masking — LEAK'
    assert '[US_SSN_' in masked, 'no SSN placeholder in masked output — masking did not run'
    assert elapsed < PII_INLET_TOTAL_BUDGET_S, 'completed, but only by exceeding the budget it was allowed'
