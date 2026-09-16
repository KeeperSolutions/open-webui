"""
Unit tests for PII masking of file and tool sources before they reach the LLM.

The tests cover `apply_source_context_to_messages` and the masking helpers behind
it: fail-closed guards, team policy, long-document chunking, retries, the masking
budget and deadline, the per-chat masked-source cache and progress events.

The external Presidio pipeline is mocked by patching `aiohttp.ClientSession` in
the middleware module, as in test_pii_toggle.py, so each test sees the request
body and controls the response. Async code runs through `asyncio.run()`.

Real file uploads, vault state and the pipeline's own detection quality live in
the pipeline and are not covered here.
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

# The splitter is shared with the prompt path.
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
    """Patch `aiohttp.ClientSession` in the middleware module with a fake pipeline.

    behavior:
      "echo"   -> 200, returns the request body unchanged (no PII found)
      "mask"   -> 200, sets messages[0].content to `masked_text`
      "refuse" -> session.post raises ClientConnectionError (pipeline down)

    detections: when set, the response carries this list as
      metadata.pii_detections_public (chunk-relative {type,start,end}).

    delay / inflight: when set, each POST sleeps `delay` seconds inside
      __aenter__ and records how many POSTs are in flight, so a test can check
      that sub-chunks are masked concurrently and within a bound.
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
# Fail-closed: pipeline unreachable
# ---------------------------------------------------------------------------


def test_u1_fail_closed_file_connection_refused():
    """A file source raises PiiMaskingBlockedError when the pipeline refuses the connection."""
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
    """Tool-shaped sources raise when the pipeline is down, like file sources.

    No caller passes tool sources to this hook today: `process_chat_response`
    renders them as citation markers without content. The test keeps the hook
    fail-closed for any future caller.
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
# Keyless filter: fail closed on zero successful masks, not on filter presence
# ---------------------------------------------------------------------------


def test_u3_keyless_filter_blocks():
    """A filter whose urlIdx has no API key is skipped, so nothing is masked and the hook raises."""
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
    """A 200 response with unchanged text (no PII found) is not blocked, because
    the guard counts successful POSTs, not changes to the text."""
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
# Missing chat_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_chat_id", [None, ""])
def test_u6_chat_id_missing_blocks(bad_chat_id):
    """Without a chat_id there is no vault to restore placeholders from, so the hook raises."""
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
# No filter pipeline configured
# ---------------------------------------------------------------------------


def test_u7_empty_filters_pii_expected_blocks():
    """With no filter pipeline configured and masking expected, the hook raises."""
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
    """With no filter pipeline and masking switched off, the text passes through unmasked.

    The opt-out only holds when team policy does not mandate masking. The policy
    is pinned to not enforced because the resolver fails closed (enforced) when
    it cannot read the policy from a MagicMock request.
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
# Request contract: chat_id and source marker sent, masked text spliced back
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
    # The request body carries chat_id and the file-source marker.
    assert body["metadata"]["chat_id"] == "chat-XYZ"
    assert body["metadata"]["pii_source"] == {
        "type": "file",
        "name": "doc.pdf",
        "file_id": "file-1",
        "note_id": None,
    }
    assert body["messages"][0]["content"] == "John Smith SSN 123-45-6789"

    # The masked text replaces the original in the returned messages.
    dumped = json.dumps(result)
    assert "[PERSON_1] SSN [US_SSN_1]" in dumped
    assert "John Smith" not in dumped


# ---------------------------------------------------------------------------
# Retrieval chunks and full-context documents; one session per hook call
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
        # One POST per document chunk in both modes; the hook does not branch on mode.
        assert len(captured) == expected_posts
        # One ClientSession for the whole hook call.
        assert mock_session_cls.call_count == 1


# ---------------------------------------------------------------------------
# No sources: messages untouched
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
    assert captured == []  # nothing sent to the pipeline


# ---------------------------------------------------------------------------
# File detections: returned, tagged with their file, never carry values
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
        # Tagged with file and chunk so the frontend can slice the value locally.
        assert d["fileId"] == "file-1"
        assert d["fileName"] == "doc.pdf"
        assert d["docIdx"] == 0
        # Only {type,start,end} plus the tags; never the detected value.
        assert set(d.keys()) == {"type", "start", "end", "fileId", "fileName", "docIdx"}
        assert "value" not in d and "original" not in d
    assert {d["type"] for d in file_pii} == {"PERSON", "US_SSN"}


# ---------------------------------------------------------------------------
# Long-document chunking and format parity
#
# One pipeline call analyses only the start of its input (about 512 tokens).
# Every document is split into pieces below that limit before masking, so the
# same text is masked the same way whether it arrives as one TXT document or as
# PDF pages.
# ---------------------------------------------------------------------------

import re

# Detectors the simulated pipeline uses. EMAIL and 11-digit OIB are unambiguous,
# never contain a newline, and give an exact expected count.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_OIB_RE = re.compile(r"\b\d{11}\b")
# Characters the simulated pipeline analyses per call, standing in for its
# token limit.
_SIM_CAP = 2048


def _long_document():
    """A multi-paragraph document longer than two `_SIM_CAP` windows, with an
    email in every paragraph and an OIB in every second one, so PII sits past
    the simulated limit and in several chunks."""
    filler = (
        "The review covered access logs, backup schedules and the escalation "
        "procedure agreed with the operations team. "
    )
    paragraphs = []
    for i in range(13):
        pii = f"Contact: person{i}@example.hr"
        if i % 2 == 0:
            pii += f", OIB {10000000000 + i * 7919}"
        paragraphs.append(f"Section {i + 1}. {filler * 3}{pii}.")
    return "\n\n".join(paragraphs)


def _detect(text):
    """Deterministic stand-in for the pipeline: returns (masked_text, detections).

    Scans only the first `_SIM_CAP` characters, like the real pipeline's token
    limit, and returns the rest unchanged. Offsets are chunk-relative."""
    scanned = text[:_SIM_CAP]
    spans = []
    for rx, typ in ((_EMAIL_RE, "EMAIL"), (_OIB_RE, "HR_OIB")):
        for m in rx.finditer(scanned):
            spans.append({"type": typ, "start": m.start(), "end": m.end()})
    spans.sort(key=lambda d: d["start"])
    # Redact spans in the scanned region, then append the unscanned tail unchanged.
    out, cur = [], 0
    for d in spans:
        out.append(scanned[cur : d["start"]])
        out.append(f"[{d['type']}]")
        cur = d["end"]
    out.append(scanned[cur:])
    out.append(text[_SIM_CAP:])
    return "".join(out), spans


def _patch_mw_session_capped(captured):
    """Patch `aiohttp.ClientSession` with a fake pipeline that masks each POST via `_detect`.

    A single input longer than `_SIM_CAP` characters keeps its tail unmasked;
    chunks below that limit are masked in full."""

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
    """Every (type, value) the simulated detectors find in the full document,
    without the per-call limit. This is the expected result for any format."""
    vals = set()
    for rx, typ in ((_EMAIL_RE, "EMAIL"), (_OIB_RE, "HR_OIB")):
        for m in rx.finditer(text):
            vals.add((typ, m.group()))
    return vals


def _reconstruct(file_pii, docs_by_idx):
    """Slice each detection's value out of the unmasked document it points at
    (docIdx) using its {start,end}, as the frontend does."""
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
    """Pieces join back to exactly the original text, stay within the chunk
    size, and never cut a detected entity."""
    text = _long_document()
    pieces = _split_text_for_pii(text, PII_MASK_CHUNK_CHARS)
    assert len(pieces) > 1  # the document spans several chunks
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
    """A whole document sent as one TXT source is masked in full, including the
    entities past the simulated per-call limit."""
    text = _long_document()
    file_pii, captured, _ = _call(_file_sources(text, name="doc.txt"))

    # The document is longer than one chunk, so it is masked with several POSTs.
    assert len(captured) > 1
    got = _reconstruct(file_pii, {0: text})
    expected = _expected_entities(text)
    assert got == expected, f"missing tail entities: {expected - got}"
    tail = {entity for entity in expected if text.index(entity[1]) >= _SIM_CAP}
    assert tail, "the document must place PII past the simulated limit"
    assert tail <= got


def test_u15_pdf_vs_txt_format_parity():
    """The same content as one TXT document and as several PDF pages yields the
    same set and number of detected entities."""
    text = _long_document()

    # TXT: whole document arrives as one source document.
    txt_sources = _file_sources(text, name="doc.txt")
    # PDF: the loader returns one document per page. Split the same text into
    # page-sized blocks on paragraph boundaries so no entity is cut.
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
    """The reassembled document sent to the model contains none of the original
    PII, including entities past the simulated per-call limit."""
    text = _long_document()
    _file_pii, _captured, result = _call(_file_sources(text, name="doc.txt"))
    dumped = json.dumps(result)
    for _typ, val in _expected_entities(text):
        assert val not in dumped, f"unmasked PII leaked into model context: {val}"


# ---------------------------------------------------------------------------
# Retry of transient POST failures at chat time
# ---------------------------------------------------------------------------


def _patch_mw_session_flaky(captured, *, fail_first, masked_text=None):
    """Patch the pipeline session so the first ``fail_first`` POSTs raise a transient error.

    Later POSTs echo the body, or mask it when ``masked_text`` is set. Returns two
    patches; the second makes ``asyncio.sleep`` a no-op so retry backoff does not
    slow the test."""
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
    """A transient POST failure that clears within PII_MASK_POST_RETRIES attempts
    does not block: the chunk is retried and the masked text reaches the LLM."""
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
    """A failure on every attempt still fails closed: PiiMaskingBlockedError is
    raised after exactly PII_MASK_POST_RETRIES POSTs and no text is returned."""
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
# Team policy and the user's masking toggle
# ---------------------------------------------------------------------------


def test_u19_team_policy_overrides_a_user_who_disabled_masking():
    """A mandated team policy overrides `features.pii_masking = False` for file
    text, as `process_pipeline_inlet_filter` does for the prompt.

    Otherwise a user under a mandated policy could switch the toggle off and send
    an attachment's contents to the LLM unmasked.
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
    """Without a mandated policy, `pii_masking = False` still skips the pipeline,
    so the policy is not an unconditional override."""
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
# Concurrency and the masking budget, shared with the prompt path
# ---------------------------------------------------------------------------


def test_u21_document_chunks_are_masked_concurrently_and_boundedly():
    """Sub-chunks of a document are masked concurrently, at most
    PII_INLET_CONCURRENCY at a time.

    The test checks real overlap (wall clock well below the sequential sum) and
    the bound, so a large document cannot open an unbounded fan-out against a
    pipeline that runs NER on a single thread.
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
    assert inflight["peak"] > 1, "chunks were masked one at a time"
    assert inflight["peak"] <= PII_INLET_CONCURRENCY, (
        f"fan-out exceeded PII_INLET_CONCURRENCY: {inflight['peak']}"
    )
    assert elapsed < len(captured) * delay * 0.75, (
        f"no real overlap: {elapsed:.2f}s against a {len(captured) * delay:.2f}s sequential sum"
    )


def test_u22_source_text_past_the_masking_budget_is_refused_before_any_post():
    """Source text past `max_maskable_chars()`, the limit the prompt path also
    uses, is refused before any POST instead of masking for minutes and failing."""
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


def test_u23_a_document_within_the_budget_is_masked_not_refused():
    """An attachment is admitted up to `max_maskable_chars()`, the same limit as
    a pasted prompt, so a 60 000-character document is masked."""
    from open_webui.utils.pii_chunking import max_maskable_chars

    doc = "x" * 60000
    assert len(doc) <= max_maskable_chars(), "the test document must fit the budget"

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
    """The budget applies to the whole request, so two documents that each fit
    but together exceed it are refused. A per-source check would let every added
    attachment add its full cost again."""
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
# Per-chat masked-source cache
#
# Sources are rebuilt on every request, so an unchanged attachment would be
# masked again on each turn. Masked text is cached per chat and keyed by
# document content. The negative tests keep the cache from leaking: another chat
# has a different vault, edited text is a new document, an opted-out turn has no
# masked text to keep, and a failed run leaves no entry.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pii_source_cache():
    """Reset the process-global cache around each test so a test never serves
    another test's masked text."""
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
    """The second turn with the same document sends no POST and returns identical
    masked text and detections."""
    sources = _file_sources("John Smith")

    first, first_posts = _mask_call(sources)
    second, second_posts = _mask_call(sources)

    assert first_posts, "the first turn must actually mask"
    assert second_posts == [], "the second turn re-masked identical text"
    assert json.dumps(first[0]) == json.dumps(second[0])
    assert first[1] == second[1], "detections must survive the cache unchanged"


def test_u26_a_different_chat_never_reuses_another_chats_masked_text():
    """Placeholders come from a vault keyed by chat_id, and another chat's vault
    cannot restore them. Masked text is therefore never reused across chats."""
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
    """With masking off the text passes through unmasked, so it is not cached.
    Serving it to a later turn with masking on would send raw PII to the LLM."""
    sources = _file_sources("John Smith")

    off, off_posts = _mask_call(sources, features={"pii_masking": False})
    assert off_posts == [], "nothing should be posted when the user opted out"
    assert "John Smith" in json.dumps(off[0])

    on, on_posts = _mask_call(sources, features={"pii_masking": True})
    assert on_posts, "the opted-out pass-through was cached and reused while masking was on"
    assert "John Smith" not in json.dumps(on[0])


def test_u29_a_failed_masking_run_is_not_remembered():
    """A masking run that raised leaves no cache entry, so the next turn masks
    again instead of serving a partial result."""
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
    """A cache hit still tags every detection with fileId, fileName and docIdx,
    which the card needs. The tags are added outside the cached value."""
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


# ---------------------------------------------------------------------------
# Masking progress for attachments
#
# Attachments report progress per sub-chunk through the same `on_progress`
# callback and status event as the prompt path.
# ---------------------------------------------------------------------------


def _progress_call(sources, *, chat_id="chat-1", captured=None):
    """Run the hook with a recording progress callback. Returns [(done, total)]."""
    progress: list = []
    captured = [] if captured is None else captured
    with _patch_mw_session(captured, behavior="echo"), _patch_policy(False):
        _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                sources,
                "q",
                chat_id=chat_id,
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
                on_progress=lambda done, total: progress.append((done, total)),
            )
        )
    return progress


def test_u31_progress_counts_sub_chunks_not_documents():
    """Progress counts sub-chunks, not documents. One attachment is many POSTs,
    so a per-document count would stay at 0/1 for the whole wait."""
    text = "x" * (PII_MASK_CHUNK_CHARS * 3 + 10)
    expected_pieces = len(_split_text_for_pii(text))
    assert expected_pieces > 1, "fixture must actually split"

    progress = _progress_call(_file_sources(text))

    assert progress, "a chunked document reported no progress at all"
    assert {t for _d, t in progress} == {expected_pieces}, "the total must not move"
    assert sorted(d for d, _t in progress) == list(range(1, expected_pieces + 1))


def test_u32_the_total_spans_every_attachment_not_each_one_separately():
    """The progress total covers every attachment in the turn, so the count does
    not restart for the second file."""
    text = "y" * (PII_MASK_CHUNK_CHARS * 2 + 10)
    per_file = len(_split_text_for_pii(text))
    sources = _file_sources(text, name="a.pdf", file_id="f1") + _file_sources(
        text + "z", name="b.pdf", file_id="f2"
    )

    progress = _progress_call(sources)

    totals = {t for _d, t in progress}
    assert len(totals) == 1, f"the total changed mid-run: {totals}"
    assert totals.pop() >= per_file * 2


def test_u33_cached_documents_are_not_counted_as_work():
    """Cached documents are not counted as work, so a fully cached turn emits no
    progress."""
    text = "x" * (PII_MASK_CHUNK_CHARS * 3 + 10)
    sources = _file_sources(text)

    first = _progress_call(sources)
    assert first, "the first turn does the work and must report it"

    posts: list = []
    second = _progress_call(sources, captured=posts)
    assert posts == [], "guard: the second turn must be a cache hit"
    assert second == [], "a fully cached turn reported masking progress it never did"


def test_u34_a_broken_progress_callback_cannot_break_masking():
    """An exception from the progress callback does not stop masking. Progress is
    informational; masking must still complete."""
    text = "x" * (PII_MASK_CHUNK_CHARS * 2 + 10)

    def _boom(done, total):
        raise RuntimeError("status channel died")

    captured = []
    with _patch_mw_session(captured, behavior="mask", masked_text="[PERSON_1]"), _patch_policy(
        False
    ):
        result, _, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                _file_sources(text),
                "q",
                chat_id="chat-boom",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
                on_progress=_boom,
            )
        )

    assert captured, "masking must still have run"
    assert "[PERSON_1]" in json.dumps(result)


# ---------------------------------------------------------------------------
# Progress emitter: `_pii_progress_emitter` and `_should_emit_pii_progress`
#
# These tests cover the emitter itself. The attachment tests above cover the
# code that calls it.
# ---------------------------------------------------------------------------

import open_webui.utils.middleware as M

def test_chat_path_emits_a_pii_masking_status_event():
    """Progress becomes `pii_masking` status events, and the final event has
    `done: True` so the UI stops showing masking as in progress.

    Runs inside an event loop because the emitter schedules events with
    `asyncio.create_task`; production callers always run inside a loop."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 2)
        on_progress(2, 2)
        await asyncio.sleep(0)  # let the scheduled tasks run
        await asyncio.sleep(0)

    asyncio.run(drive())

    assert [e['data']['action'] for e in events] == ['pii_masking', 'pii_masking']
    assert events[0]['data']['done'] is False
    assert events[-1]['data']['done'] is True
    assert events[-1]['data']['count'] == events[-1]['data']['total'] == 2

def test_progress_swallows_a_synchronously_raising_emitter():
    """`on_progress` swallows an `event_emitter` that raises synchronously. It is
    called from the chunk-masking tasks, so an escaping exception would fail the
    whole masking request."""
    import open_webui.utils.middleware as M

    def emitter(event):
        raise RuntimeError('boom')

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 1)  # must not raise

    asyncio.run(drive())  # must not raise

def test_progress_retrieves_a_raising_coroutines_exception_via_the_done_callback():
    """An exception raised inside a scheduled emission is retrieved by
    `_pii_progress_task_done`, so the loop's exception handler is never called.

    The emission runs in its own task, so the exception never reaches
    `on_progress` either way. An unretrieved exception would only show up in
    the exception handler when the task is garbage-collected, which is what
    this test checks for."""
    import gc

    import open_webui.utils.middleware as M

    async def emitter(event):
        raise RuntimeError('boom')

    handler_calls = []

    async def drive():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: handler_calls.append(context))

        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 1)  # must not raise
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Drop any remaining task references and collect garbage, so an
        # unretrieved exception would reach the exception handler now.
        M._pii_progress_tasks.clear()
        gc.collect()

    asyncio.run(drive())  # must not raise

    assert handler_calls == [], (
        "the coroutine's exception must be retrieved via the done-callback, "
        f"not surfaced to asyncio's default exception handler: {handler_calls}"
    )

def test_progress_swallows_a_malformed_total_raised_by_the_throttle_guard_itself():
    """A `TypeError` from the throttle check on a non-comparable `total` is
    swallowed by `on_progress`, because the check runs inside its `try`.

    `done=2` is required: with `done=1` the `done <= 1` test short-circuits
    before `total` is compared. An escaping exception would fail the
    chunk-masking task and the whole masking request."""
    import open_webui.utils.middleware as M

    async def emitter(event):
        pass

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(2, None)  # must not raise — done=2 skips the done<=1 short-circuit

    asyncio.run(drive())  # must not raise

def test_progress_throttles_to_about_twenty_events_and_always_emits_the_terminal_one():
    """Progress is throttled to about twenty events per run, and the first and
    final events are always emitted.

    Each status event rewrites the chat row without a lock
    (`Chats.add_message_status_to_chat_by_id_and_message_id`), so per-chunk
    events on a large document mean many rewrites. The final event must not be
    dropped, or the status stays in progress in the UI."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        for done in range(1, 101):
            on_progress(done, 100)
        for _ in range(200):
            await asyncio.sleep(0)

    asyncio.run(drive())

    counts = [e['data']['count'] for e in events]
    assert counts[0] == 1, 'first completion must always be reported'
    assert counts[-1] == 100, 'last event reported must be the terminal one'
    assert events[-1]['data']['done'] is True, 'terminal event must be marked done'
    assert 2 <= len(events) <= 21, f'expected roughly twenty throttled events, got {len(events)}'

def test_progress_on_a_small_total_still_emits_first_and_last_without_dividing_by_zero():
    """For a small `total` the throttle does not fail with a modulo-by-zero
    error and still reports the first and terminal events."""
    import open_webui.utils.middleware as M

    events = []

    async def emitter(event):
        events.append(event)

    async def drive():
        on_progress = M._pii_progress_emitter(emitter)
        on_progress(1, 3)
        on_progress(2, 3)
        on_progress(3, 3)
        # Emissions are chained, so wait until every scheduled one has run.
        while M._pii_progress_tasks:
            await asyncio.sleep(0)

    asyncio.run(drive())  # must not raise (no ZeroDivisionError)

    counts = [e['data']['count'] for e in events]
    assert counts[0] == 1
    assert counts[-1] == 3
    assert events[-1]['data']['done'] is True


# ---------------------------------------------------------------------------
# Masking deadline
#
# The budget check in `mask_sources_for_llm` is an estimate made before the
# first POST. The run itself is also bounded by PII_INLET_TOTAL_BUDGET_S, so a
# pipeline slower than the estimate ends in a refusal instead of holding the
# chat request open.
# ---------------------------------------------------------------------------


def _patch_budget(seconds):
    return patch("open_webui.utils.middleware.PII_INLET_TOTAL_BUDGET_S", seconds)


def test_u41_masking_that_outruns_the_budget_is_stopped_not_left_running():
    """A slow pipeline must end in a bounded refusal, not an open-ended wait."""
    captured = []
    with _patch_mw_session(captured, behavior="echo", delay=2.0), _patch_policy(
        False
    ), _patch_budget(1.0):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    _file_sources("John Smith lives in Zagreb"),
                    "q",
                    chat_id="chat-slow",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )
    assert captured, "guard: the estimate must have let this through so the deadline is what fired"


def test_u42_a_run_stopped_by_the_deadline_caches_nothing():
    """A run stopped by the deadline leaves no cache entry, so the next turn
    cannot serve a half-finished result."""
    sources = _file_sources("John Smith lives in Zagreb")

    with _patch_mw_session([], behavior="echo", delay=2.0), _patch_policy(False), _patch_budget(
        1.0
    ):
        with pytest.raises(PiiMaskingBlockedError):
            _run(
                apply_source_context_to_messages(
                    _make_request(),
                    [{"role": "user", "content": "q"}],
                    sources,
                    "q",
                    chat_id="chat-slow",
                    user=_make_user(),
                    model_id="gpt-4",
                    models=_make_models(),
                    features={"pii_masking": True},
                )
            )

    _, posts = _mask_call(sources, chat_id="chat-slow")
    assert posts, "a run killed by the deadline poisoned the cache"


def test_u43_a_normal_run_is_not_cut_short_by_the_deadline():
    """The other half: the deadline must bound the pathological case without
    touching a healthy one."""
    captured = []
    with _patch_mw_session(captured, behavior="mask", masked_text="[PERSON_1]"), _patch_policy(
        False
    ), _patch_budget(30):
        result, _, _ = _run(
            apply_source_context_to_messages(
                _make_request(),
                [{"role": "user", "content": "q"}],
                _file_sources("John Smith"),
                "q",
                chat_id="chat-ok",
                user=_make_user(),
                model_id="gpt-4",
                models=_make_models(),
                features={"pii_masking": True},
            )
        )
    assert "[PERSON_1]" in json.dumps(result)


def test_u44_the_budget_does_not_charge_for_documents_it_will_not_mask():
    """The budget estimate counts only documents that are not already cached.

    A cached attachment costs no masking time, so it must not push a turn past
    the limit. The deadline bounds real work, and the estimate must agree with it.
    """
    from open_webui.utils.pii_chunking import max_maskable_chars

    cap = max_maskable_chars()
    big = "a" * int(cap * 0.7)
    extra = "b" * int(cap * 0.5)
    assert len(big) + len(extra) > cap, "fixture must exceed the cap when summed"

    warm = _file_sources(big, name="a.pdf", file_id="f1")
    _mask_call(warm)  # turn 1: masks and caches the big one

    both = warm + _file_sources(extra, name="b.pdf", file_id="f2")
    result, _ = _mask_call(both)
    assert "[PERSON_1]" in json.dumps(result), "refused a turn for work it was not going to do"


# ---------------------------------------------------------------------------
# Only PII filters count as masking, and the cache follows what the pipeline
# was told
# ---------------------------------------------------------------------------


def _filter(filter_id, url_idx=0, targets=("*",), priority=0):
    return {
        "id": filter_id,
        "urlIdx": url_idx,
        "pipeline": {"type": "filter", "priority": priority, "pipelines": list(targets)},
    }


def _apply(
    sources,
    *,
    captured,
    behavior,
    models,
    model_id="gpt-4",
    user=None,
    features=None,
    request=None,
):
    with _patch_mw_session(captured, behavior=behavior, masked_text="[PERSON_1]"), _patch_policy(
        False
    ):
        return _run(
            apply_source_context_to_messages(
                request or _make_request(),
                [{"role": "user", "content": "q"}],
                sources,
                "q",
                chat_id="chat-1",
                user=user or _make_user(),
                model_id=model_id,
                models=models,
                features=features if features is not None else {"pii_masking": True},
            )
        )


def test_a_non_pii_filter_alone_is_not_accepted_as_masking():
    """When only a non-PII filter applies to the model, the hook refuses before
    any POST, so the raw attachment reaches neither that filter nor the LLM."""
    captured = []
    models = {"gpt-4": {"id": "gpt-4"}, "telemetry_filter": _filter("telemetry_filter")}

    with pytest.raises(PiiMaskingBlockedError):
        _apply(_file_sources("John Smith"), captured=captured, behavior="echo", models=models)

    assert captured == [], "the raw text was sent to a non-PII filter"


def test_a_skipped_pii_filter_is_not_covered_by_another_filter_answering():
    """A PII filter without an API key is skipped. Another filter answering does
    not count as masking, so the hook still refuses."""
    models = {
        "gpt-4": {"id": "gpt-4"},
        "pii_filter": _filter("pii_filter", url_idx=0),
        "telemetry_filter": _filter("telemetry_filter", url_idx=1, priority=1),
    }
    request = _make_request(
        base_urls=["http://pii-host", "http://telemetry-host"], api_keys=["", "secret-key"]
    )

    with pytest.raises(PiiMaskingBlockedError):
        _apply(
            _file_sources("John Smith"),
            captured=[],
            behavior="echo",
            models=models,
            request=request,
        )


def test_a_turn_without_the_masking_flag_is_not_cached():
    """Without `features.pii_masking` the pipeline receives the user's stored
    valve, which can be False, so its unchanged text is not cached for a later
    turn with masking on."""
    sources = _file_sources("John Smith")
    user = _make_user(pii_enabled=False)

    raw_posts = []
    raw = _apply(
        sources,
        captured=raw_posts,
        behavior="echo",
        models=_make_models(),
        user=user,
        features={},
    )
    assert raw_posts[0]["user"]["valves"]["pii_masking_enabled"] is False
    assert "John Smith" in json.dumps(raw[0])

    on_posts = []
    on = _apply(sources, captured=on_posts, behavior="mask", models=_make_models(), user=user)
    assert on_posts, "text returned under the stored valve was served from the cache"
    assert "John Smith" not in json.dumps(on[0])


def test_a_model_with_a_different_pii_filter_masks_the_document_again():
    """Filters can target specific models, so switching to a model with a
    different PII filter masks the document again. The same model still reuses
    the cached text."""
    models = {
        "model-a": {"id": "model-a"},
        "model-b": {"id": "model-b"},
        "pii_filter": _filter("pii_filter", targets=("model-a",)),
        "pii_filter_pipeline": _filter("pii_filter_pipeline", targets=("model-b",)),
    }
    sources = _file_sources("John Smith")

    first, second, third = [], [], []
    _apply(sources, captured=first, behavior="mask", models=models, model_id="model-a")
    _apply(sources, captured=second, behavior="mask", models=models, model_id="model-b")
    _apply(sources, captured=third, behavior="mask", models=models, model_id="model-b")

    assert first and second, "model-b reused text masked by model-a's filter"
    assert third == [], "the same model and filters must reuse the cached text"


# ---------------------------------------------------------------------------
# A turn that carries attachments and no typed message
# ---------------------------------------------------------------------------


def test_attachment_only_prompt_stands_in_for_an_empty_message():
    """A turn with attachments and no text gets a short model-facing message, so
    the source context and the masking that goes with it are not skipped."""
    from open_webui.utils.middleware import _attachment_only_prompt

    assert _attachment_only_prompt(_file_sources("John Smith")) == "Attached file"
    assert (
        _attachment_only_prompt(_file_sources("John Smith") + _file_sources("Jane Doe"))
        == "Attached files"
    )


def test_attachment_only_prompt_never_carries_a_file_name():
    """The text is set after the inlet has masked the turn, so a file name must
    not reach the model: the name itself can contain PII."""
    from open_webui.utils.middleware import _attachment_only_prompt

    sources = _file_sources("John Smith", name="john-doe-cv.pdf", file_id="f1")

    assert "john-doe" not in _attachment_only_prompt(sources)


def test_attachment_only_prompt_is_empty_without_sources():
    """With no sources there is nothing to stand in for, so the message is left
    as it is."""
    from open_webui.utils.middleware import _attachment_only_prompt

    assert _attachment_only_prompt([]) == ""
