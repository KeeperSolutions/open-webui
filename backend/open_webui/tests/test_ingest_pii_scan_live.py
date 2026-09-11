"""LIVE end-to-end check of the ingest PII scan against a REAL pipeline (TRAU-513).

Opt-in: skipped unless KEEPER_PII_LIVE=1, so CI and the default suite never reach
out to the network. Reads the pipeline connection from the local OWUI database
(the same place the running backend reads it from), so there is no hardcoded URL
or key anywhere in the repo.

What it proves that the mocked tests cannot: that a real document actually comes
back with detections, and that the offsets we store are usable — the PII card
slices values straight out of the stored content using them, so an off-by-N here
is a wrong card, and a cross-chunk rebasing bug is invisible to any mock.

    KEEPER_PII_LIVE=1 pytest open_webui/tests/test_ingest_pii_scan_live.py -q -s
"""

import asyncio
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
HOST_MATCH = os.environ.get('KEEPER_PII_LIVE_HOST', 'pipelines-v4--staging')

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
    row = (
        sqlite3.connect(os.path.abspath(DB_PATH)).execute('SELECT data FROM config ORDER BY id DESC LIMIT 1').fetchone()
    )
    openai = json.loads(row[0]).get('openai', {})
    for idx, (url, key) in enumerate(zip(openai.get('api_base_urls', []), openai.get('api_keys', []))):
        if HOST_MATCH in url:
            return idx, url.rstrip('/'), key
    pytest.skip(f'no connection matching {HOST_MATCH!r} in {DB_PATH}')


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
    """A document deliberately LONGER than PII_MASK_CHUNK_CHARS, with PII placed in
    the first chunk and in a later one, so cross-chunk offset rebasing is exercised."""
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

    # Every detection must carry a span that indexes INTO the document — this is
    # the contract the PII card relies on when it slices values client-side.
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

    # OIB is the flagship Croatian recognizer (checksum-validated) and sits in the
    # FIRST chunk; the email sits in the LAST. Requiring both pins down that the
    # scan covers the whole document, not just its head.
    assert 'OIB' in found, f'OIB not recovered by offset; got {sorted(sliced)[:10]}'
    assert 'EMAIL' in found, 'tail PII not recovered — offsets not rebased across chunks'


def test_live_scan_is_a_no_op_without_a_pii_filter():
    """Same live wiring, filter removed: the scan must degrade to [] rather than
    raise — ingest is best-effort and must never block an upload."""
    request, _ = _request_and_models()
    models = {'gpt-4': {'id': 'gpt-4'}}
    assert (
        asyncio.run(
            scan_file_content_for_pii(request, _document(), file_id='live-no-filter', user=_user(), models=models)
        )
        == []
    )
