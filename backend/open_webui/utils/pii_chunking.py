"""Shared sizing and splitting for PII masking calls (TRAU-543).

Deliberately dependency-free (no FastAPI, no aiohttp) so both `routers/` and
`utils/` can import it without a cycle, and so the splitter is unit-testable on
its own.

Every constant here is derived from ONE measurement (see
`pii_scripts/TRAU-543-PROMPT-PII-CHUNKING-PLAN.md`, Appendix A): the external
inlet processes text at a flat ~240 characters/second, independent of how much
you send it. Do not tune these by intuition — re-measure.
"""

# Characters per masking call. NOT a throughput knob: bigger chunks do not go
# faster (the rate is flat), they only eat the per-request margin — 6000 chars
# already measures ~25 s against a 30 s budget. 1800 chars is ~8 s.
PII_INLET_CHUNK_CHARS = 1800

# Sub-chunk POSTs in flight at once. The wall-clock floor for a request is
# total_chars / 240 / concurrency, so this is the ONLY lever that shortens a
# large paste: 150 000 chars is ~625 s serial and ~65 s at 10. Kept modest
# because the pipeline is a scale-to-zero Cloud Run service and a bigger fan-out
# buys cold starts instead of throughput.
PII_INLET_CONCURRENCY = 10

# Wall-clock ceiling for masking ONE request, across all its chunks. Without it
# nothing bounds total time once the work is parallel — the per-request socket
# timeout only bounds a single POST. On expiry the request is refused
# (fail-closed), never forwarded.
PII_INLET_TOTAL_BUDGET_S = 120

# Per-chunk retries for transient failures (cold start, 5xx, dropped
# connection). Retrying one ~8 s chunk is far cheaper than failing a whole turn.
PII_INLET_CHUNK_RETRIES = 3


def split_text_for_pii(text, max_chars=PII_INLET_CHUNK_CHARS):
    """Partition ``text`` into pieces no longer than ``max_chars`` whose
    concatenation is EXACTLY ``text`` (lossless — no dropped or duplicated
    chars). Breaks are taken at paragraph / line / space boundaries, in that
    order of preference, so a multi-token PII span (credit card, IBAN, phone
    number) is never cut across a piece boundary. Only when a single run of
    non-whitespace exceeds ``max_chars`` is a hard mid-run split used.

    Returns a list of ``(start_offset, piece)`` tuples where ``start_offset`` is
    the piece's character offset within ``text``.
    """
    if not isinstance(text, str) or len(text) <= max_chars:
        return [(0, text)]

    pieces = []
    i, n = 0, len(text)
    while i < n:
        if n - i <= max_chars:
            pieces.append((i, text[i:]))
            break
        window_end = i + max_chars
        split_at = -1
        for sep in ('\n\n', '\n', ' '):
            idx = text.rfind(sep, i, window_end)
            if idx > i:
                split_at = idx + len(sep)  # keep the separator with the piece
                break
        if split_at <= i:
            split_at = window_end  # no boundary in window -> hard split
        pieces.append((i, text[i:split_at]))
        i = split_at
    return pieces
