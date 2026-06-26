"""Unit tests for the ingest-time best-effort full-file PII scan (TRAU-513).
Mirrors test_file_attachment_pii.py: the external Presidio inlet is MOCKED by
patching middleware.aiohttp.ClientSession; the scan opens its own session."""
import asyncio, copy, json, sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
sys.modules.setdefault("stripe", MagicMock())
import aiohttp
from open_webui.utils.middleware import (
    scan_file_content_for_pii,
    _resolve_pii_scan_model_id,
    PII_MASK_CHUNK_CHARS,
)

def _models(filter_id="pii_filter", url_idx=0, with_filter=True):
    models = {"gpt-4": {"id": "gpt-4"}}
    if with_filter:
        models[filter_id] = {"id": filter_id, "urlIdx": url_idx,
            "pipeline": {"type": "filter", "priority": 0, "pipelines": ["*"]}}
    return models

def _request():
    r = MagicMock()
    r.app.state.config.OPENAI_API_BASE_URLS = ["http://pipeline-host"]
    r.app.state.config.OPENAI_API_KEYS = ["secret-key"]
    r.app.state.MODELS = _models()
    return r

def _user():
    return SimpleNamespace(id="u1", email="t@e.com", name="T", role="user", settings=None)

def _patch_session(captured, cap=2048):
    """Simulated CAPPED Presidio: scans only first `cap` chars, returns
    {type,start,end} for 11-digit OIBs in the scanned region."""
    import re
    OIB = re.compile(r"\b\d{11}\b")
    def _cm(req):
        body = copy.deepcopy(req["body"])
        text = body["messages"][0]["content"][:cap]
        dets = [{"type": "HR_OIB", "start": m.start(), "end": m.end()} for m in OIB.finditer(text)]
        body["messages"][0]["content"] = "[masked]"
        body.setdefault("metadata", {})["pii_detections_public"] = dets
        resp = MagicMock(); resp.json = AsyncMock(return_value=body)
        resp.raise_for_status = MagicMock(); resp.content_type = "application/json"
        cm = MagicMock(); cm.__aenter__ = AsyncMock(return_value=resp); cm.__aexit__ = AsyncMock(return_value=False)
        return cm
    def _post(url, *, headers, json, ssl):
        captured.append(json); return _cm(json)
    s = MagicMock(); s.post = _post
    scm = MagicMock(); scm.__aenter__ = AsyncMock(return_value=s); scm.__aexit__ = AsyncMock(return_value=False)
    return patch("open_webui.utils.middleware.aiohttp.ClientSession", return_value=scm)

def test_resolve_model_picks_filter_applicable_model():
    assert _resolve_pii_scan_model_id(_models()) == "gpt-4"

def test_resolve_model_none_when_no_filter():
    assert _resolve_pii_scan_model_id(_models(with_filter=False)) is None

def test_scan_long_content_covers_whole_file():
    # 3 OIBs spread across >2 chunks; the tail OIB sits past the 2048 cap.
    head = "OIB 11111111111 " + ("x" * 1900)
    mid  = " OIB 22222222222 " + ("y" * 1900)
    tail = " OIB 33333333333 end"
    content = head + mid + tail
    captured = []
    with _patch_session(captured):
        dets = asyncio.run(scan_file_content_for_pii(
            _request(), content, file_id="f1", user=_user(), models=_models()))
    starts = sorted(d["start"] for d in dets)
    assert [content[s:s+11] for s in starts] == ["11111111111","22222222222","33333333333"]
    assert all(set(d.keys()) == {"type","start","end"} for d in dets)
    assert len(captured) > 1  # sub-chunked, so tail was not truncated

def test_scan_best_effort_never_raises_on_presidio_down():
    captured = []
    with patch("open_webui.utils.middleware.aiohttp.ClientSession") as cls:
        cls.return_value.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError("down"))
        dets = asyncio.run(scan_file_content_for_pii(
            _request(), "OIB 11111111111", file_id="f1", user=_user(), models=_models()))
    assert dets == []  # best-effort: swallow, return empty, never block ingest

def test_scan_empty_or_nonstr_content_returns_empty():
    captured = []
    with _patch_session(captured):
        assert asyncio.run(scan_file_content_for_pii(
            _request(), "", file_id="f1", user=_user(), models=_models())) == []
        assert asyncio.run(scan_file_content_for_pii(
            _request(), 42, file_id="f1", user=_user(), models=_models())) == []
    assert captured == []  # guard returns before any POST

def test_scan_skips_when_no_pii_filter():
    captured = []
    with _patch_session(captured):
        dets = asyncio.run(scan_file_content_for_pii(
            _request(), "OIB 11111111111", file_id="f1", user=_user(),
            models=_models(with_filter=False)))
    assert dets == [] and captured == []
