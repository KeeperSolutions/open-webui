"""End-to-end check of the ingest PII scan against a running PII pipeline.

Skipped unless KEEPER_PII_LIVE=1, so CI and the default suite never use the
network. The pipeline URL and key are read from the local Open WebUI database
(KEEPER_PII_LIVE_DB), from the connection whose URL contains KEEPER_PII_LIVE_HOST,
so no URL or key is stored in the repo. Once KEEPER_PII_LIVE=1 is set, a database
or connection that cannot be used fails the test and names what it found, rather
than skipping.

It checks what the mocked tests cannot: that a real pipeline returns detections,
and that the stored offsets select the right values across chunks. The PII card
slices values out of the stored content with these offsets.

    KEEPER_PII_LIVE=1 pytest open_webui/tests/test_ingest_pii_scan_live.py -q -s
"""

import asyncio
import contextlib
import json
import os
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('stripe', MagicMock())

from open_webui.utils.middleware import (  # noqa: E402
    PII_MASK_CHUNK_CHARS,
    scan_file_content_for_pii,
)

pytestmark = pytest.mark.skipif(
    os.environ.get('KEEPER_PII_LIVE') != '1',
    reason='live pipeline test; set KEEPER_PII_LIVE=1 to run',
)

DB_PATH = os.environ.get(
    'KEEPER_PII_LIVE_DB',
    os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'webui.db'),
)
HOST_MATCH = os.environ.get('KEEPER_PII_LIVE_HOST', 'pipelines-v4')

# Synthetic Croatian PII. Each value is unique and appears verbatim exactly once,
# so an offset can be checked by slicing the document.
NEEDLES = {
    'OIB': '12345678903',
    'IBAN': 'HR1210010051863000160',
    'EMAIL': 'ivan.horvat@example.com',
    'PHONE': '+385 91 234 5678',
}
FILLER = (
    'Ovaj dokument opisuje uvjete poslovne suradnje, rokove isporuke i '
    'obveze ugovornih strana prema vazecim propisima Republike Hrvatske. '
)


def _connection():
    """Return (index, url, key) of the first connection whose URL contains HOST_MATCH.

    Every failure here reports the URLs that were found and the variable that
    selects one, so an unusable database cannot look like a test that ran."""
    path = os.path.abspath(DB_PATH)
    if not os.path.isfile(path):
        pytest.fail(f'no Open WebUI database at {path}; set KEEPER_PII_LIVE_DB to one')

    try:
        with contextlib.closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db:
            row = db.execute('SELECT data FROM config ORDER BY id DESC LIMIT 1').fetchone()
    except sqlite3.Error as err:
        pytest.fail(f'cannot read the config table of {path}: {err}')
    if row is None:
        pytest.fail(f'{path} holds no config row; open Open WebUI once to write one')

    openai = json.loads(row[0]).get('openai', {})
    urls = openai.get('api_base_urls', [])
    for idx, (url, key) in enumerate(zip(urls, openai.get('api_keys', []))):
        if HOST_MATCH in url:
            if not key:
                pytest.fail(f'connection {url} has no API key in {path}')
            return idx, url.rstrip('/'), key
    pytest.fail(
        f'no connection URL of {path} contains {HOST_MATCH!r}; found {urls}. '
        f'Set KEEPER_PII_LIVE_HOST to a substring of the pipeline connection you want.'
    )


def _request_and_models():
    idx, url, key = _connection()
    urls, keys = [''] * (idx + 1), [''] * (idx + 1)
    urls[idx], keys[idx] = url, key
    models = {
        'gpt-4': {'id': 'gpt-4'},
        'pii_filter_pipeline': {
            'id': 'pii_filter_pipeline',
            'urlIdx': idx,
            'pipeline': {'type': 'filter', 'priority': 0, 'pipelines': ['*']},
        },
    }
    request = MagicMock()
    request.app.state.config.OPENAI_API_BASE_URLS = urls
    request.app.state.config.OPENAI_API_KEYS = keys
    request.app.state.MODELS = models
    return request, models


def _user():
    return SimpleNamespace(id='live-test', email='live@example.com', name='Live', role='user', settings=None)


def _document():
    """Build a document longer than PII_MASK_CHUNK_CHARS with PII in the first and
    a later chunk, so offset rebasing across chunks is exercised."""
    head = f'Ugovorna strana: Ivan Horvat, OIB {NEEDLES["OIB"]}, IBAN {NEEDLES["IBAN"]}.\n\n'
    middle = FILLER * ((PII_MASK_CHUNK_CHARS * 2) // len(FILLER) + 1)
    tail = f'\n\nKontakt: {NEEDLES["EMAIL"]}, telefon {NEEDLES["PHONE"]}.\n'
    return head + middle + tail


def test_live_ingest_scan_detects_pii_with_usable_offsets():
    content = _document()
    request, models = _request_and_models()

    detections = asyncio.run(
        scan_file_content_for_pii(request, content, file_id='live-test-file', user=_user(), models=models)
    )

    assert detections, 'live pipeline returned no detections for a document full of PII'

    # Every span must lie inside the document; the PII card slices values
    # client-side with these offsets.
    for d in detections:
        assert set(d) == {'type', 'start', 'end'}, f'unexpected keys: {sorted(d)}'
        assert 0 <= d['start'] < d['end'] <= len(content), f'span outside document: {d}'

    sliced = {content[d['start'] : d['end']] for d in detections}

    # The tail values prove offsets were rebased from chunk-relative to
    # document-relative: they live past the first chunk boundary.
    assert len(content) > PII_MASK_CHUNK_CHARS, 'document must span several chunks'
    found = {name: needle for name, needle in NEEDLES.items() if needle in sliced}
    print(f'\n  document: {len(content)} chars, {len(detections)} detections')
    print(f'  recovered by offset: {sorted(found)}')
    print(f'  missed: {sorted(set(NEEDLES) - set(found))}')

    # The OIB (a checksum-validated Croatian ID) is in the first chunk and the
    # email in the last. Requiring both shows the scan covers the whole document.
    assert 'OIB' in found, f'OIB not recovered by offset; got {sorted(sliced)[:10]}'
    assert 'EMAIL' in found, 'tail PII not recovered; offsets not rebased across chunks'


def test_live_scan_is_a_no_op_without_a_pii_filter():
    """Without a PII filter the scan returns [] instead of raising, because ingest
    is best-effort and must never block an upload."""
    request, _ = _request_and_models()
    models = {'gpt-4': {'id': 'gpt-4'}}
    assert (
        asyncio.run(
            scan_file_content_for_pii(request, _document(), file_id='live-no-filter', user=_user(), models=models)
        )
        == []
    )
