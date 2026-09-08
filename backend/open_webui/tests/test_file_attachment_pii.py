"""
Tier 1 unit tests for Task 3.6 file/tool-attachment PII masking (approach 3a).

Covers the OWUI-side hook only, with the external Presidio pipeline MOCKED:
  - apply_source_context_to_messages (async; per-chunk masking before <source> wrap)
  - _mask_text_via_pii_pipeline (fail-closed clone of process_pipeline_inlet_filter)
  - C.1 keyless-filter guard (block on zero successful masks when masking expected)
  - C.2 single aiohttp session per hook call

Mirrors the mocking idiom of test_pii_toggle.py: we patch aiohttp.ClientSession
(in the middleware module) so `async with session.post(...) as resp` captures the
request_data and returns a controllable response. Tests invoke the async hook via
asyncio.run() (no pytest-asyncio markers, matching the existing suite).

The E2E concerns (real PDF/DOCX upload, live Postgres vault state, cross-thread
placeholder consistency, chunk-boundary detection) are NOT unit-testable here —
the masking/vault logic lives in Presidio, which is mocked. Those are Tier 2.
"""

import asyncio
import copy
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# stripe is an optional billing dependency not installed in the test environment.
# Mock it before any open_webui import triggers the import chain.
sys.modules.setdefault("stripe", MagicMock())

import aiohttp

from open_webui.utils.middleware import (
    apply_source_context_to_messages,
    _mask_text_via_pii_pipeline,  # noqa: F401 (imported to assert it exists / is reusable)
    _mask_long_text_via_pii_pipeline,  # noqa: F401
    PII_MASK_CHUNK_CHARS,
    PII_MASK_POST_RETRIES,
    PiiMaskingBlockedError,
)

# The splitter now has ONE implementation, shared with the prompt path
# (TRAU-543); middleware's private copy was byte-identical and is gone.
from open_webui.utils.pii_chunking import split_text_for_pii as _split_text_for_pii


# ---------------------------------------------------------------------------
# Helpers (mirror test_pii_toggle.py)
# ---------------------------------------------------------------------------


def _make_user(pii_enabled=None, filter_id="pii_filter"):
    settings_dict = {}
    if pii_enabled is not None:
        settings_dict = {
            "ui": {"pipelines": {"valves": {filter_id: {"pii_masking_enabled": pii_enabled}}}}
        }

    class _Settings:
        def model_dump(self):
            return settings_dict

    return SimpleNamespace(
        id="user-1",
        email="test@example.com",
        name="Test User",
        role="user",
        settings=_Settings() if pii_enabled is not None else None,
    )


def _make_request(base_urls=None, api_keys=None, rag_template="[context]\n{{CONTEXT}}"):
    request = MagicMock()
    request.app.state.config.OPENAI_API_BASE_URLS = base_urls or ["http://pipeline-host"]
    request.app.state.config.OPENAI_API_KEYS = (
        api_keys if api_keys is not None else ["secret-key"]
    )
    request.app.state.config.RAG_TEMPLATE = rag_template
    return request


def _make_models(filter_id="pii_filter", url_idx=0, with_filter=True):
    models = {"gpt-4": {"id": "gpt-4"}}
    if with_filter:
        models[filter_id] = {
            "id": filter_id,
            "urlIdx": url_idx,
            "pipeline": {"type": "filter", "priority": 0, "pipelines": ["*"]},
        }
    return models


def _file_sources(text, *, src_type="file", name="doc.pdf", file_id="file-1"):
    return [
        {
            "source": {"type": src_type, "name": name, "id": file_id},
            "document": [text],
            "metadata": [{"file_id": file_id, "source": name}],
        }
    ]


def _run(coro):
    return asyncio.run(coro)


def _patch_policy(enforced):
    """Pin the team PII-masking policy for one test.

    Patched in the middleware namespace because that is where the name is bound
    (`from open_webui.routers.pipelines import resolve_pii_masking_enforced`);
    patching the router module would not affect the already-imported reference.
    """
    return patch(
        "open_webui.utils.middleware.resolve_pii_masking_enforced",
        AsyncMock(return_value=enforced),
    )


def _patch_mw_session(
    captured: list, *, behavior="echo", masked_text=None, detections=None, delay=None, inflight=None
):
    """Patch middleware.aiohttp.ClientSession.

    behavior:
      "echo"   -> 200, returns request body unchanged (filter found no PII)
      "mask"   -> 200, returns body with messages[0].content = masked_text
      "refuse" -> session.post raises ClientConnectionError (Presidio down)

    detections: when set, the response body's metadata.pii_detections_public is
      populated with this list (chunk-relative {type,start,end}) so the hook's
      B2 detection-collection path can be exercised.

    delay / inflight: when set, each POST sleeps `delay` inside __aenter__ and
      records how many are in flight, so a test can assert that sub-chunks are
      masked concurrently AND that the fan-out stays bounded.
    """
    inflight = inflight if inflight is not None else {"now": 0, "peak": 0}

    def _make_response_cm(request_data):
        body = request_data["body"]
        if behavior == "mask" or detections is not None:
            body = copy.deepcopy(body)
        if behavior == "mask":
            body["messages"][0]["content"] = masked_text
        if detections is not None:
            body.setdefault("metadata", {})["pii_detections_public"] = detections
        resp = MagicMock()
        resp.json = AsyncMock(return_value=body)
        resp.raise_for_status = MagicMock()
        resp.content_type = "application/json"
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def _fake_post(url, *, headers, json, ssl):
        captured.append(json)
        if behavior == "refuse":
            raise aiohttp.ClientConnectionError("connection refused")
        cm = _make_response_cm(json)
        if delay:
            resp = cm.__aenter__.return_value

            async def _enter(_self=None):
                # _self: unittest.mock wraps a plain function assigned to a
                # dunder as func(self, ...), so the mock passes `cm` itself.
                inflight["now"] += 1
                inflight["peak"] = max(inflight["peak"], inflight["now"])
                await asyncio.sleep(delay)  # the await must live inside __aenter__
                inflight["now"] -= 1
                return resp

            cm.__aenter__ = _enter
        return cm

    session = MagicMock()
    session.post = _fake_post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    return patch(
        "open_webui.utils.middleware.aiohttp.ClientSession", return_value=session_cm
    )


# ---------------------------------------------------------------------------
# U1–U2  Fail-closed: Presidio unreachable
# ---------------------------------------------------------------------------


def test_u1_fail_closed_file_connection_refused():
    """File path: Presidio refuses -> hook raises PiiMaskingBlockedError (propagates)."""
    captured = []
    with _patch_mw_session(captured, behavior="refuse"):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    _file_sources("John Smith SSN 123-45-6789"),
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )


def test_u2_fail_closed_tool_shaped_sources_raise():
    """Tool path: hook raises on tool-sourced text when Presidio is down.

    The call-site (middleware.py:3486) catches this PiiMaskingBlockedError,
    emits chat:message:error and skips the follow-up LLM dispatch — that wiring
    runs inside process_chat_response's streaming handler and is asserted at the
    integration/E2E layer, not here. This test locks the *contract* the call-site
    relies on: tool-sourced masking failures raise (never return unmasked).
    """
    captured = []
    tool_sources = [
        {
            "source": {"type": "tool", "name": "search_web", "id": "search_web"},
            "document": ["Jane Doe email jane@example.com"],
            "metadata": [{"source": "search_web", "name": "search_web"}],
        }
    ]
    with _patch_mw_session(captured, behavior="refuse"):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    tool_sources,
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )


# ---------------------------------------------------------------------------
# U3–U4  C.1 keyless guard — fail-closed on OUTCOME, not on presence
# ---------------------------------------------------------------------------


def test_u3_keyless_filter_blocks():
    """Filter present but its urlIdx has no API key -> zero masks -> BLOCK."""
    captured = []
    with _patch_mw_session(captured, behavior="echo"):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(api_keys=[""]),  # empty key for urlIdx 0
                    [{"role": "user", "content": "q"}],
                    _file_sources("John Smith"),
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )
    assert captured == []  # keyless filter is skipped before any POST


def test_u4_unchanged_response_counts_as_pass():
    """INVERSE of U3: a 200 with unchanged text (no PII found) is a valid pass.

    Locks the semantics 'count successful POSTs, not text changes'.
    """
    captured = []
    msgs = [{"role": "user", "content": "q"}]
    with _patch_mw_session(captured, behavior="echo"):
        result, _, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                msgs,
                _file_sources("clean text no pii"),
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )
    assert len(captured) == 1  # one successful POST -> not blocked
    assert any("clean text no pii" in json.dumps(m) for m in result)


# ---------------------------------------------------------------------------
# U6  chat_id missing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_chat_id", [None, ""])
def test_u6_chat_id_missing_blocks(bad_chat_id):
    """No thread-vault key -> cannot mask restorably -> BLOCK."""
    captured = []
    with _patch_mw_session(captured, behavior="echo"):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    _file_sources("John Smith"),
                    "q",
                    chat_id=bad_chat_id,
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )


# ---------------------------------------------------------------------------
# U7–U8  Empty-filter semantics (Decision 2)
# ---------------------------------------------------------------------------


def test_u7_empty_filters_pii_expected_blocks():
    """No filter pipeline configured AND masking expected -> BLOCK."""
    captured = []
    with _patch_mw_session(captured, behavior="echo"):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    _file_sources("John Smith"),
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(with_filter=False),
                    features={"pii_masking": True},
                )
            )


def test_u8_empty_filters_not_pii_expected_passes():
    """No filter pipeline AND masking explicitly disabled -> benign PASS.

    The opt-out is only valid while team policy does not mandate masking, so
    that precondition is now pinned explicitly rather than left to whatever the
    resolver makes of a MagicMock request (it fails closed, i.e. ENFORCED).
    """
    captured = []
    msgs = [{"role": "user", "content": "q"}]
    with _patch_mw_session(captured, behavior="echo"), _patch_policy(False):
        result, _, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                msgs,
                _file_sources("John Smith"),
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(with_filter=False),
                features={"pii_masking": False},
            )
        )
    assert captured == []  # nothing routed to Presidio
    assert any("John Smith" in json.dumps(m) for m in result)  # passed through unmasked


# ---------------------------------------------------------------------------
# U9  Propagation contract: chat_id + file-source marker out, masked text back 1:1
# ---------------------------------------------------------------------------


def test_u9_propagation_contract():
    captured = []
    with _patch_mw_session(
        captured, behavior="mask", masked_text="[PERSON_1] SSN [US_SSN_1]"
    ):
        result, file_pii, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                _file_sources("John Smith SSN 123-45-6789", name="doc.pdf", file_id="file-1"),
                "q",
                chat_id="chat-XYZ",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )

    assert len(captured) == 1
    body = captured[0]["body"]
    # Hook SENDS chat_id + file-source marker in the synthetic body.
    assert body["metadata"]["chat_id"] == "chat-XYZ"
    assert body["metadata"]["pii_source"] == {
        "type": "file",
        "name": "doc.pdf",
        "file_id": "file-1",
        "note_id": None,
    }
    assert body["messages"][0]["content"] == "John Smith SSN 123-45-6789"

    # Hook SPLICES the masked reply back; original PII is gone from final messages.
    dumped = json.dumps(result)
    assert "[PERSON_1] SSN [US_SSN_1]" in dumped
    assert "John Smith" not in dumped


# ---------------------------------------------------------------------------
# U10  Mode-agnostic (retrieval chunks vs full-context single doc) + C.2 session
# ---------------------------------------------------------------------------


def test_u10_mode_agnostic_single_session():
    retrieval = [
        {
            "source": {"type": "file", "name": "d.pdf", "id": "f1"},
            "document": ["chunk A John", "chunk B Mary"],
            "metadata": [{"file_id": "f1"}, {"file_id": "f1"}],
        }
    ]
    full = [
        {
            "source": {"type": "file", "name": "d.pdf", "id": "f1"},
            "document": ["chunk A John chunk B Mary"],
            "metadata": [{"file_id": "f1"}],
        }
    ]
    for sources, expected_posts in [(retrieval, 2), (full, 1)]:
        captured = []
        with _patch_mw_session(captured, behavior="echo") as mock_session_cls:
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    sources,
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )
        # One POST per document chunk in BOTH modes (hook does not branch on mode).
        assert len(captured) == expected_posts
        # C.2: exactly one ClientSession opened for the whole hook call.
        assert mock_session_cls.call_count == 1


# ---------------------------------------------------------------------------
# U11  Backward compat: no sources -> untouched
# ---------------------------------------------------------------------------


def test_u11_no_sources_untouched():
    msgs = [{"role": "user", "content": "hello"}]
    captured = []
    with _patch_mw_session(captured, behavior="echo"):
        result, _, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                msgs,
                [],  # no sources
                "hello",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )
    assert result == msgs  # returned unchanged
    assert captured == []  # no masking attempted, no session opened


# ---------------------------------------------------------------------------
# U12  B2: file-sourced detections returned, tagged, value-free (trust boundary)
# ---------------------------------------------------------------------------


def test_u12_file_detections_collected_and_tagged():
    captured = []
    # Pipeline returns chunk-relative detections for the file chunk.
    dets = [
        {"type": "PERSON", "start": 17, "end": 27},
        {"type": "US_SSN", "start": 33, "end": 44},
    ]
    with _patch_mw_session(
        captured, behavior="mask", masked_text="masked", detections=dets
    ):
        _result, file_pii, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                _file_sources(
                    "Employee record: John Smith, SSN 123-45-6789",
                    name="doc.pdf",
                    file_id="file-1",
                ),
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )
    assert len(file_pii) == 2
    for d in file_pii:
        # tagged with file + chunk so the frontend can reconstruct locally
        assert d["fileId"] == "file-1"
        assert d["fileName"] == "doc.pdf"
        assert d["docIdx"] == 0
        # boundary: pipeline gives only {type,start,end}; NEVER a value/original
        assert set(d.keys()) == {"type", "start", "end", "fileId", "fileName", "docIdx"}
        assert "value" not in d and "original" not in d
    assert {d["type"] for d in file_pii} == {"PERSON", "US_SSN"}


# ---------------------------------------------------------------------------
# U13–U16  Long-document chunking — format parity (TRAU-513 truncation bug)
#
# A single external-pipeline masking call truncates its input at the pipeline's
# tokenizer cap (~512 tokens). Before the fix, a source document longer than the
# cap was sent as ONE blob and its tail was silently dropped: the same text as a
# PDF (arriving pre-chunked per page, each page < cap) masked MORE entities than
# as a single TXT blob. These tests pin the fix: every document is split into
# sub-chunks under the cap before masking, so coverage is format-independent.
# ---------------------------------------------------------------------------

import re
from pathlib import Path

# Repo-root/pii_scripts/e2e_files/test-pii-dokument.txt — the byte-for-byte
# fixture the PDF/TXT discrepancy was first observed on.
_E2E_TXT = (
    Path(__file__).resolve().parents[3]
    / "pii_scripts"
    / "e2e_files"
    / "test-pii-dokument.txt"
)

# Detectors the simulated pipeline uses. EMAIL + 11-digit OIB are unambiguous,
# never contain a newline, and let us assert an exact expected count.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_OIB_RE = re.compile(r"\b\d{11}\b")
# The cap the live pipeline applies (~512 tokens). For the real fixture, 512
# tokens lands near char 2973; 2048 is a conservative stand-in that still leaves
# >40% of the document past the cutoff.
_SIM_CAP = 2048


def _detect(text):
    """Deterministic stand-in for Presidio: returns (masked_text, public_dets)
    over `text`, but ONLY scans the first `_SIM_CAP` chars — modelling the live
    pipeline's silent tokenizer truncation. Offsets are chunk-relative."""
    scanned = text[:_SIM_CAP]
    spans = []
    for rx, typ in ((_EMAIL_RE, "EMAIL"), (_OIB_RE, "HR_OIB")):
        for m in rx.finditer(scanned):
            spans.append({"type": typ, "start": m.start(), "end": m.end()})
    spans.sort(key=lambda d: d["start"])
    # Build masked text: redact detected spans in the SCANNED region, keep the
    # (unscanned) tail verbatim — mirrors a pipeline that only masks what it saw.
    out, cur = [], 0
    for d in spans:
        out.append(scanned[cur : d["start"]])
        out.append(f"[{d['type']}]")
        cur = d["end"]
    out.append(scanned[cur:])
    out.append(text[_SIM_CAP:])
    return "".join(out), spans


def _patch_mw_session_capped(captured):
    """Patch middleware.aiohttp.ClientSession with a simulated CAPPED Presidio:
    each POST is masked via `_detect`, which only scans the first `_SIM_CAP`
    chars (silent truncation). This is what makes a too-long single blob lose its
    tail while many small chunks do not."""

    def _make_cm(request_data):
        body = copy.deepcopy(request_data["body"])
        text = body["messages"][0]["content"]
        masked, dets = _detect(text)
        body["messages"][0]["content"] = masked
        body.setdefault("metadata", {})["pii_detections_public"] = dets
        resp = MagicMock()
        resp.json = AsyncMock(return_value=body)
        resp.raise_for_status = MagicMock()
        resp.content_type = "application/json"
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def _fake_post(url, *, headers, json, ssl):
        captured.append(json)
        return _make_cm(json)

    session = MagicMock()
    session.post = _fake_post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "open_webui.utils.middleware.aiohttp.ClientSession", return_value=session_cm
    )


def _expected_entities(text):
    """The full set of (type, value) the simulated detectors find over the WHOLE
    document (no cap) — the ground truth the fix must recover regardless of
    format."""
    vals = set()
    for rx, typ in ((_EMAIL_RE, "EMAIL"), (_OIB_RE, "HR_OIB")):
        for m in rx.finditer(text):
            vals.add((typ, m.group()))
    return vals


def _reconstruct(file_pii, docs_by_idx):
    """Mirror the frontend: slice each detection's value out of the ORIGINAL doc
    chunk it points at (docIdx) using the doc-relative {start,end}."""
    out = set()
    for d in file_pii:
        doc = docs_by_idx[d["docIdx"]]
        out.add((d["type"], doc[d["start"] : d["end"]]))
    return out


def _call(sources):
    captured = []
    with _patch_mw_session_capped(captured):
        _result, file_pii, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                sources,
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )
    return file_pii, captured, _result


def test_u13_split_helper_partitions_losslessly():
    """The splitter is the core of the fix: pieces must concatenate back to the
    exact original (no dropped/duplicated chars) and stay within the budget,
    breaking only on whitespace so PII spans are never cut."""
    text = _E2E_TXT.read_text(encoding="utf-8")
    pieces = _split_text_for_pii(text, PII_MASK_CHUNK_CHARS)
    assert len(pieces) > 1  # the fixture is longer than one budget
    assert "".join(p for _, p in pieces) == text  # lossless
    offset = 0
    for start, piece in pieces:
        assert start == offset
        offset += len(piece)
        assert len(piece) <= PII_MASK_CHUNK_CHARS
    # No detected entity straddles a piece boundary.
    boundaries = set()
    acc = 0
    for _, p in pieces[:-1]:
        acc += len(p)
        boundaries.add(acc)
    for rx in (_EMAIL_RE, _OIB_RE):
        for m in rx.finditer(text):
            for b in boundaries:
                assert not (m.start() < b < m.end()), (
                    f"entity {m.group()!r} cut at piece boundary {b}"
                )


def test_u14_long_txt_blob_no_tail_truncation():
    """RED before fix: a single full-document TXT source, under a CAPPED pipeline,
    must still detect entities from the tail (past the cap). Without sub-chunking
    the single blob is truncated and the tail entities vanish."""
    text = _E2E_TXT.read_text(encoding="utf-8")
    file_pii, captured, _ = _call(_file_sources(text, name="doc.txt"))

    # The blob exceeded one budget -> more than one masking POST was issued.
    assert len(captured) > 1
    got = _reconstruct(file_pii, {0: text})
    expected = _expected_entities(text)
    assert got == expected, f"missing tail entities: {expected - got}"
    # Specifically: a witness email that lives well past the cap is recovered.
    assert ("EMAIL", "ravnatelj@jadran-fin.hr") in got
    assert ("EMAIL", "i.babic@example.hr") in got


def test_u15_pdf_vs_txt_format_parity():
    """Acceptance criterion: identical content as a single TXT blob and as a
    multi-'page' PDF yields the SAME number and SET of masked entities."""
    text = _E2E_TXT.read_text(encoding="utf-8")

    # TXT: whole document arrives as one source document.
    txt_sources = _file_sources(text, name="doc.txt")
    # PDF: the loader hands us one document PER PAGE. Split the same text into
    # page-sized blocks on paragraph boundaries (no entity cut).
    paras = text.split("\n\n")
    pages, buf = [], ""
    for p in paras:
        if len(buf) + len(p) > 1200 and buf:
            pages.append(buf)
            buf = ""
        buf += (p + "\n\n")
    if buf:
        pages.append(buf)
    pdf_sources = [
        {
            "source": {"type": "file", "name": "doc.pdf", "id": "file-1"},
            "document": pages,
            "metadata": [{"file_id": "file-1"} for _ in pages],
        }
    ]

    txt_pii, _, _ = _call(txt_sources)
    pdf_pii, _, _ = _call(pdf_sources)

    txt_vals = _reconstruct(txt_pii, {0: text})
    pdf_vals = _reconstruct(pdf_pii, {i: pg for i, pg in enumerate(pages)})

    expected = _expected_entities(text)
    assert txt_vals == expected
    assert pdf_vals == expected
    assert txt_vals == pdf_vals
    assert len(txt_pii) == len(pdf_pii) == len(expected)


def test_u16_masked_doc_reassembled_no_pii_leak():
    """The reassembled masked document sent to the LLM must contain none of the
    original PII — including PII from the tail that used to be truncated."""
    text = _E2E_TXT.read_text(encoding="utf-8")
    _file_pii, _captured, result = _call(_file_sources(text, name="doc.txt"))
    dumped = json.dumps(result)
    for _typ, val in _expected_entities(text):
        assert val not in dumped, f"unmasked PII leaked into model context: {val}"


# ---------------------------------------------------------------------------
# U17–U18  Chat-time transient-retry (the pipeline vault snapshot occasionally
# trips its own DB command_timeout when many chunks/files hit it in one turn).
# ---------------------------------------------------------------------------


def _patch_mw_session_flaky(captured, *, fail_first, masked_text=None):
    """Patch middleware.aiohttp.ClientSession so session.post raises a transient
    error on its first ``fail_first`` calls, then succeeds (echo, or mask when
    masked_text is set). Also patches middleware.asyncio.sleep to a no-op so the
    retry backoff doesn't slow the test."""
    state = {"calls": 0}

    def _make_response_cm(request_data):
        body = request_data["body"]
        if masked_text is not None:
            body = copy.deepcopy(body)
            body["messages"][0]["content"] = masked_text
        resp = MagicMock()
        resp.json = AsyncMock(return_value=body)
        resp.raise_for_status = MagicMock()
        resp.content_type = "application/json"
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def _fake_post(url, *, headers, json, ssl):
        captured.append(json)
        state["calls"] += 1
        if state["calls"] <= fail_first:
            raise aiohttp.ClientConnectionError("transient: vault snapshot timeout")
        return _make_response_cm(json)

    session = MagicMock()
    session.post = _fake_post
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    return patch.multiple(
        "open_webui.utils.middleware.aiohttp",
        ClientSession=MagicMock(return_value=session_cm),
    ), patch("open_webui.utils.middleware.asyncio.sleep", new=AsyncMock())


def test_u17_chat_time_retry_succeeds_after_transient_failures():
    """A transient POST failure that clears within PII_MASK_POST_RETRIES must NOT
    surface a block: the chunk is retried and the masked text reaches the LLM."""
    assert PII_MASK_POST_RETRIES >= 2  # test needs headroom for a retry
    captured = []
    session_patch, sleep_patch = _patch_mw_session_flaky(
        captured, fail_first=PII_MASK_POST_RETRIES - 1, masked_text="John [REDACTED]"
    )
    with session_patch, sleep_patch:
        result, _, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                _file_sources("John Smith SSN 123-45-6789"),
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )
    # Failed (PII_MASK_POST_RETRIES-1) times, then one success = PII_MASK_POST_RETRIES POSTs.
    assert len(captured) == PII_MASK_POST_RETRIES
    assert any("[REDACTED]" in json.dumps(m) for m in result)


def test_u18_chat_time_retry_exhausted_still_fail_closed():
    """A persistent transient failure across ALL retries still fails closed:
    PiiMaskingBlockedError is raised (no unmasked text) after exactly
    PII_MASK_POST_RETRIES attempts."""
    captured = []
    session_patch, sleep_patch = _patch_mw_session_flaky(captured, fail_first=10_000)
    with session_patch, sleep_patch:
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    _file_sources("John Smith SSN 123-45-6789"),
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )
    assert len(captured) == PII_MASK_POST_RETRIES  # tried exactly N times, then blocked


# ---------------------------------------------------------------------------
# U19  Team policy overrides a user who switched masking off
# ---------------------------------------------------------------------------


def test_u19_team_policy_overrides_a_user_who_disabled_masking():
    """A mandated policy must beat `features.pii_masking = False` for FILE text
    exactly as it already does for the prompt.

    The prompt path resolves the policy and forces the valve back on
    (`routers/pipelines.py`, `if policy_enforced and filter_id in PII_FILTER_IDS`).
    This path decided from the request flag alone, so a user under a mandated
    policy could switch the toggle off and send an attachment's contents to the
    LLM unmasked — the prompt masked, the file not. That is the precise thing
    the enforcement layer exists to prevent.
    """
    captured = []
    msgs = [{"role": "user", "content": "q"}]
    with _patch_mw_session(captured, behavior="mask", masked_text="[PERSON_1]"), _patch_policy(True):
        result, _, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                msgs,
                _file_sources("John Smith"),
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": False},
            )
        )

    assert captured, "policy mandates masking; the source text must reach the pipeline"
    rendered = json.dumps(result)
    assert "John Smith" not in rendered, "file PII reached the LLM despite a mandated policy"
    assert "[PERSON_1]" in rendered


def test_u20_without_a_mandated_policy_the_user_opt_out_still_holds():
    """The other half: the policy must not become an unconditional override.
    With no mandate, `pii_masking = False` still means no masking call."""
    captured = []
    msgs = [{"role": "user", "content": "q"}]
    with _patch_mw_session(captured, behavior="mask", masked_text="[PERSON_1]"), _patch_policy(False):
        result, _, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                msgs,
                _file_sources("John Smith"),
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": False},
            )
        )

    assert captured == [], "nothing should be routed to Presidio when the user opted out"
    assert "John Smith" in json.dumps(result)


# ---------------------------------------------------------------------------
# U21-U24  Parity with the prompt path: concurrency, and a budget not a wall
# ---------------------------------------------------------------------------


def test_u21_document_chunks_are_masked_concurrently_and_boundedly():
    """The chat-time file path masked every sub-chunk one after another, so a
    document cost chunks x ~7.5s inside the chat request while the prompt path
    did the same work concurrently. Comparing the two measured two
    implementations, not two code paths.

    Both halves are asserted: the work really overlaps (wall clock far below the
    sequential sum) and it stays bounded by PII_INLET_CONCURRENCY, so a large
    document cannot open an unbounded fan-out against a pipeline that serializes
    NER on one thread anyway.
    """
    from open_webui.utils.pii_chunking import PII_INLET_CHUNK_CHARS, PII_INLET_CONCURRENCY

    delay = 0.05
    chunks = 12
    doc = ("a" * (PII_INLET_CHUNK_CHARS - 1) + "\n") * chunks
    captured = []
    inflight = {"now": 0, "peak": 0}

    started = time.monotonic()
    with _patch_mw_session(captured, behavior="echo", delay=delay, inflight=inflight):
        _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                _file_sources(doc),
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )
    elapsed = time.monotonic() - started

    assert len(captured) >= chunks, "fixture must actually split into many chunks"
    assert inflight["peak"] > 1, "chunks are still masked one at a time"
    assert inflight["peak"] <= PII_INLET_CONCURRENCY, (
        f"fan-out exceeded PII_INLET_CONCURRENCY: {inflight['peak']}"
    )
    assert elapsed < len(captured) * delay * 0.75, (
        f"no real overlap: {elapsed:.2f}s against a {len(captured) * delay:.2f}s sequential sum"
    )


def test_u22_source_text_past_the_masking_budget_is_refused_before_any_post():
    """The cap is now the same wall-clock budget the prompt path uses, not a
    fixed character wall. Past it the honest answer is an immediate refusal
    rather than a request that runs for minutes and fails anyway."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    captured = []
    with _patch_mw_session(captured, behavior="echo"):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    _file_sources("x" * (max_maskable_chars() + 1)),
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )
    assert captured == [], "a refused request must not touch the pipeline at all"


def test_u23_a_document_the_old_fixed_cap_refused_is_now_masked():
    """The regression this parity work exists for: 50 000 characters (~15 pages)
    were blocked outright by MAX_SOURCE_TEXT_CHARS while the prompt path took
    five times as much. Same text, same pipeline, opposite answer depending only
    on how the user supplied it."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    doc = "x" * 60000
    assert len(doc) > 50000, "must exceed the old fixed cap"
    assert len(doc) <= max_maskable_chars(), "the budget must admit what the wall refused"

    captured = []
    with _patch_mw_session(captured, behavior="echo"):
        _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                _file_sources(doc),
                "q",
                chat_id="chat-1",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )
    assert captured, "the document must actually be masked, not refused"


def test_u24_the_budget_is_summed_across_all_sources_not_per_source():
    """Wall clock is a property of the request, not of one attachment. Two
    documents that each fit but together do not must be refused: a per-source
    check let N attachments multiply the real cost N times over."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    half = max_maskable_chars() // 2 + 1000
    sources = _file_sources("x" * half, name="a.pdf", file_id="f1") + _file_sources(
        "y" * half, name="b.pdf", file_id="f2"
    )

    captured = []
    with _patch_mw_session(captured, behavior="echo"):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    sources,
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )
    assert captured == []


# ---------------------------------------------------------------------------
# U25-U30  Per-chat masked-source cache (TRAU-513 follow-up)
#
# Measured on staging: a 167 460-char attachment costs 106.4s of masking, and
# that cost was paid AGAIN on every later turn of the same chat — the sources
# are rebuilt per request and `metadata['masked_sources']` never outlives it.
# The document text is byte-identical each turn (the file carries
# `context: 'full'`, so retrieval hands back the whole thing rather than a
# query-dependent TOP_K slice), so the second masking run is provably pure
# waste: same input, same vault, same output.
#
# The cache must not become a leak surface, hence the negative tests: a
# different chat has a different vault (different placeholders), edited text is
# a different document, an opted-out turn never masked anything worth keeping,
# and a failed run must not be remembered as a success.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pii_source_cache():
    """The cache is process-global, so leaking it across tests would make them
    order-dependent (a later test would silently serve an earlier test's text)."""
    from open_webui.utils.middleware import reset_masked_source_cache

    reset_masked_source_cache()
    yield
    reset_masked_source_cache()


def _mask_call(sources, *, chat_id="chat-1", features=None, captured=None, policy=False):
    captured = [] if captured is None else captured
    with _patch_mw_session(captured, behavior="mask", masked_text="[PERSON_1]"), _patch_policy(
        policy
    ):
        result = _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                sources,
                "q",
                chat_id=chat_id,
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features=features if features is not None else {"pii_masking": True},
            )
        )
    return result, captured


def test_u25_the_same_document_in_the_same_chat_is_masked_once():
    """The whole point: turn 2 must cost zero POSTs and return byte-identical
    text. Anything less and 'hvala' still waits two minutes."""
    sources = _file_sources("John Smith")

    first, first_posts = _mask_call(sources)
    second, second_posts = _mask_call(sources)

    assert first_posts, "the first turn must actually mask"
    assert second_posts == [], "the second turn re-masked identical text"
    assert json.dumps(first[0]) == json.dumps(second[0])
    assert first[1] == second[1], "detections must survive the cache unchanged"


def test_u26_a_different_chat_never_reuses_another_chats_masked_text():
    """Placeholders are minted from a vault keyed by chat_id. Serving chat A's
    masked text inside chat B would hand the LLM placeholders that chat B's
    vault cannot restore on the outlet — a broken conversation, and a cache
    that silently crosses a tenancy boundary."""
    sources = _file_sources("John Smith")

    _mask_call(sources, chat_id="chat-1")
    _, posts = _mask_call(sources, chat_id="chat-2")

    assert posts, "chat-2 must mask against its own vault"


def test_u27_edited_document_text_is_masked_again():
    """The cache is keyed by content, not by file id: re-uploading a file under
    the same id with new text must not serve the old masked body."""
    _mask_call(_file_sources("John Smith"))
    _, posts = _mask_call(_file_sources("Jane Doe"))

    assert posts, "changed document text was served from the cache"


def test_u28_an_opted_out_turn_is_never_cached():
    """The dangerous case. With masking off the pipeline is not called and the
    text passes through UNMASKED. Caching that and serving it to a later turn
    where masking is ON would send raw PII to the LLM — the exact leak this
    whole path exists to prevent."""
    sources = _file_sources("John Smith")

    off, off_posts = _mask_call(sources, features={"pii_masking": False})
    assert off_posts == [], "nothing should be posted when the user opted out"
    assert "John Smith" in json.dumps(off[0])

    on, on_posts = _mask_call(sources, features={"pii_masking": True})
    assert on_posts, "the opted-out pass-through was cached and reused while masking was ON"
    assert "John Smith" not in json.dumps(on[0])


def test_u29_a_failed_masking_run_is_not_remembered():
    """Fail-closed must stay fail-closed across turns: a run that raised has no
    result worth keeping, and must not leave a partial entry behind."""
    sources = _file_sources("John Smith")

    with _patch_mw_session([], behavior="refuse"), _patch_policy(False):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    sources,
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )

    _, posts = _mask_call(sources)
    assert posts, "a failed run poisoned the cache"


def test_u30_cached_detections_are_still_tagged_with_their_file():
    """The card needs fileId/fileName/docIdx on every detection. Those are
    applied around the cached value, so a cache hit must not drop them."""
    sources = _file_sources("John Smith", name="doc.pdf", file_id="file-1")
    dets = [{"type": "PERSON", "start": 0, "end": 10}]

    captured = []
    with _patch_mw_session(
        captured, behavior="mask", masked_text="[PERSON_1]", detections=dets
    ), _patch_policy(False):
        args = (
            _make_request(),
            [{"role": "user", "content": "q"}],
            sources,
            "q",
        )
        kwargs = dict(
            chat_id="chat-1",
            user=_make_user(),
            model_id="gpt-4",
            models=_make_models(),
            features={"pii_masking": True},
        )
        _, first_dets, _ = _run(apply_source_context_to_messages(*args, **kwargs))
        posts_after_first = len(captured)
        _, second_dets, _ = _run(apply_source_context_to_messages(*args, **kwargs))

    assert len(captured) == posts_after_first, "the second call must not post"
    assert first_dets == second_dets
    assert first_dets and first_dets[0]["fileId"] == "file-1"
    assert first_dets[0]["fileName"] == "doc.pdf"
    assert first_dets[0]["docIdx"] == 0
