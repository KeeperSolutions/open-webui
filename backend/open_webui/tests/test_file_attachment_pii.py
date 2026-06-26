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
    _split_text_for_pii,
    PII_MASK_CHUNK_CHARS,
    PiiMaskingBlockedError,
)


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


def _patch_mw_session(captured: list, *, behavior="echo", masked_text=None, detections=None):
    """Patch middleware.aiohttp.ClientSession.

    behavior:
      "echo"   -> 200, returns request body unchanged (filter found no PII)
      "mask"   -> 200, returns body with messages[0].content = masked_text
      "refuse" -> session.post raises ClientConnectionError (Presidio down)

    detections: when set, the response body's metadata.pii_detections_public is
      populated with this list (chunk-relative {type,start,end}) so the hook's
      B2 detection-collection path can be exercised.
    """

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
        return _make_response_cm(json)

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
# U5  Char cap
# ---------------------------------------------------------------------------


def test_u5_char_cap_blocks_before_post():
    """Accumulated source text > 50000 -> BLOCK before any masking POST."""
    captured = []
    big = "x" * 50001
    with _patch_mw_session(captured, behavior="echo"):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    _file_sources(big),
                    "q",
                    chat_id="chat-1",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )
    assert captured == []  # cap raises before the first POST


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
    """No filter pipeline AND masking explicitly disabled -> benign PASS."""
    captured = []
    msgs = [{"role": "user", "content": "q"}]
    with _patch_mw_session(captured, behavior="echo"):
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
