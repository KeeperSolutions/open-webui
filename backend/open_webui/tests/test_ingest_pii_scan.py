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


def test_process_file_persists_detections(monkeypatch):
    """process_file's helper stores scan output under file.data['pii_detections']
    without clobbering 'content', and never propagates scan errors."""
    import open_webui.routers.retrieval as R

    saved = {}
    async def fake_update(file_id, data, db=None):
        saved.setdefault(file_id, {}).update(data); return SimpleNamespace(id=file_id)
    monkeypatch.setattr(R.Files, "update_file_data_by_id", staticmethod(fake_update))
    async def fake_scan(request, content, *, file_id, user, models=None, features=None):
        return [{"type": "HR_OIB", "start": 4, "end": 15}]
    monkeypatch.setattr(R, "scan_file_content_for_pii", fake_scan)

    asyncio.run(R._store_ingest_pii_detections(MagicMock(), "f1", "OIB 11111111111", _user()))
    assert saved["f1"]["pii_detections"] == [{"type": "HR_OIB", "start": 4, "end": 15}]
    assert "content" not in saved["f1"]  # only the detections key is written here


def test_store_ingest_pii_detections_swallows_scan_error(monkeypatch):
    """Best-effort: a scan that raises must NOT propagate out of the helper."""
    import open_webui.routers.retrieval as R
    async def raising_scan(request, content, *, file_id, user, models=None, features=None):
        raise RuntimeError("presidio down")
    monkeypatch.setattr(R, "scan_file_content_for_pii", raising_scan)
    async def noop_update(*a, **k):
        return None
    monkeypatch.setattr(R.Files, "update_file_data_by_id", staticmethod(noop_update))
    # must not raise
    asyncio.run(R._store_ingest_pii_detections(MagicMock(), "f1", "OIB 11111111111", _user()))


def test_content_endpoint_returns_detections(monkeypatch):
    """GET /{id}/data/content additively returns stored span-only detections."""
    import open_webui.routers.files as F
    file_obj = SimpleNamespace(
        id="f1", user_id="u1",
        data={"content": "OIB 11111111111", "pii_detections": [{"type": "HR_OIB", "start": 4, "end": 15}]},
    )
    async def fake_get(id, db=None):
        return file_obj
    monkeypatch.setattr(F.Files, "get_file_by_id", staticmethod(fake_get))
    out = asyncio.run(F.get_file_data_content_by_id("f1", user=_user(), db=None))
    assert out["content"] == "OIB 11111111111"
    assert out["pii_detections"] == [{"type": "HR_OIB", "start": 4, "end": 15}]


def test_content_endpoint_detections_default_empty(monkeypatch):
    """A file with no stored detections returns an empty list, not a missing key."""
    import open_webui.routers.files as F
    file_obj = SimpleNamespace(id="f1", user_id="u1", data={"content": "hello"})
    async def fake_get(id, db=None):
        return file_obj
    monkeypatch.setattr(F.Files, "get_file_by_id", staticmethod(fake_get))
    out = asyncio.run(F.get_file_data_content_by_id("f1", user=_user(), db=None))
    assert out["content"] == "hello"
    assert out["pii_detections"] == []


def test_scan_partial_chunk_failure_keeps_other_detections():
    """Best-effort + parallel: if ONE sub-chunk's POST fails, detections from the
    other chunks must survive (the old sequential path lost everything on the
    first failure)."""
    import re

    head = "OIB 11111111111 " + ("x" * 1900)
    bad = " FAILCHUNK " + ("y" * 1900)
    tail = " OIB 33333333333 end"
    content = head + bad + tail
    OIB = re.compile(r"\b\d{11}\b")

    def _cm(req):
        body = copy.deepcopy(req["body"])
        text = body["messages"][0]["content"][:2048]
        dets = [{"type": "HR_OIB", "start": m.start(), "end": m.end()} for m in OIB.finditer(text)]
        body["messages"][0]["content"] = "[m]"
        body.setdefault("metadata", {})["pii_detections_public"] = dets
        resp = MagicMock(); resp.json = AsyncMock(return_value=body)
        resp.raise_for_status = MagicMock(); resp.content_type = "application/json"
        cm = MagicMock(); cm.__aenter__ = AsyncMock(return_value=resp); cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def _post(url, *, headers, json, ssl):
        if "FAILCHUNK" in json["body"]["messages"][0]["content"]:
            raise aiohttp.ClientConnectionError("boom")
        return _cm(json)

    s = MagicMock(); s.post = _post
    scm = MagicMock(); scm.__aenter__ = AsyncMock(return_value=s); scm.__aexit__ = AsyncMock(return_value=False)
    with patch("open_webui.utils.middleware.aiohttp.ClientSession", return_value=scm):
        dets = asyncio.run(scan_file_content_for_pii(
            _request(), content, file_id="f1", user=_user(), models=_models()))
    vals = sorted(content[d["start"]:d["end"]] for d in dets)
    assert "11111111111" in vals and "33333333333" in vals


def test_scan_caps_large_content(monkeypatch):
    """Above PII_SCAN_MAX_CHARS only the prefix is scanned; tail PII is skipped,
    and the surviving offsets still index correctly into the full content."""
    import open_webui.utils.middleware as M

    monkeypatch.setattr(M, "PII_SCAN_MAX_CHARS", 1000)
    content = "OIB 11111111111 " + ("x" * 1500) + " OIB 33333333333 end"
    captured = []
    with _patch_session(captured):
        dets = asyncio.run(scan_file_content_for_pii(
            _request(), content, file_id="f1", user=_user(), models=_models()))
    vals = {content[d["start"]:d["end"]] for d in dets}
    assert "11111111111" in vals       # within the cap -> scanned
    assert "33333333333" not in vals   # beyond the cap -> skipped


def test_scan_retries_transient_chunk_failure():
    """A chunk whose call fails ONCE then succeeds must be RECOVERED, not dropped.
    Guards against the non-deterministic under-count (same file -> 85/60/25)."""
    import re

    head = "OIB 11111111111 " + ("x" * 1900)
    flap = " OIB 22222222222 " + ("y" * 1900)  # this chunk fails once, then succeeds
    content = head + flap
    OIB = re.compile(r"\b\d{11}\b")
    calls = {"n": 0}

    def _cm(req):
        body = copy.deepcopy(req["body"])
        text = body["messages"][0]["content"][:2048]
        dets = [{"type": "HR_OIB", "start": m.start(), "end": m.end()} for m in OIB.finditer(text)]
        body["messages"][0]["content"] = "[m]"
        body.setdefault("metadata", {})["pii_detections_public"] = dets
        resp = MagicMock(); resp.json = AsyncMock(return_value=body)
        resp.raise_for_status = MagicMock(); resp.content_type = "application/json"
        cm = MagicMock(); cm.__aenter__ = AsyncMock(return_value=resp); cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def _post(url, *, headers, json, ssl):
        if "22222222222" in json["body"]["messages"][0]["content"]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise aiohttp.ClientConnectionError("transient")
        return _cm(json)

    s = MagicMock(); s.post = _post
    scm = MagicMock(); scm.__aenter__ = AsyncMock(return_value=s); scm.__aexit__ = AsyncMock(return_value=False)
    with patch("open_webui.utils.middleware.aiohttp.ClientSession", return_value=scm):
        dets = asyncio.run(scan_file_content_for_pii(
            _request(), content, file_id="f1", user=_user(), models=_models()))
    vals = {content[d["start"]:d["end"]] for d in dets}
    assert "11111111111" in vals
    assert "22222222222" in vals      # recovered via retry (would be dropped without it)
    assert calls["n"] >= 2            # proves the chunk was retried
