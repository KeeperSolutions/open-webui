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

# Sub-chunk POSTs in flight at once. In THEORY the wall-clock floor for a
# request is total_chars / rate / concurrency, so this looks like the lever
# that shortens a large paste. In PRACTICE, measured 2026-09-04 against the
# staging pipeline, concurrent requests queue behind one saturated CPU instead
# of landing on separate instances — see `PII_INLET_EFFECTIVE_SPEEDUP`, the
# ~1.3x that models what this constant actually buys today, not the ~10x this
# comment used to (wrongly) imply. Kept at 10 anyway: it costs nothing while
# the service doesn't scale out, and pays off immediately the day it does
# (Cloud Run instance limits, CPU allocation, GPU — infrastructure work outside
# this repo) without any code change here.
PII_INLET_CONCURRENCY = 10

# Wall-clock ceiling for masking ONE request, across all its chunks. Without it
# nothing bounds total time once the work is parallel — the per-request socket
# timeout only bounds a single POST. On expiry the request is refused
# (fail-closed), never forwarded.
PII_INLET_TOTAL_BUDGET_S = 120

# Per-chunk retries for transient failures (cold start, 5xx, dropped
# connection). Retrying one ~8 s chunk is far cheaper than failing a whole turn.
PII_INLET_CHUNK_RETRIES = 3

# Measured inlet throughput, characters per second (Appendix A). Everything
# below is arithmetic on this number; re-measure before changing it.
PII_INLET_CHARS_PER_SECOND = 240

# How much wall-clock concurrency ACTUALLY buys us, measured 2026-09-04 against
# the staging pipeline: one request alone 8.13 s, four concurrent 25.22 s total
# — a 1.29x speedup where perfect scale-out would be 4.0x. The service does not
# scale out; concurrent requests queue behind one saturated CPU. Deliberately
# NOT `PII_INLET_CONCURRENCY`: using the concurrency here would overstate
# capacity roughly eightfold and let prompts in that would then hit the deadline
# and be refused anyway. When the pipeline is allowed to scale out (Cloud Run
# instance limits, CPU allocation, GPU — infrastructure work outside this repo),
# this is the one number to raise.
PII_INLET_EFFECTIVE_SPEEDUP = 1.3


def estimated_masking_seconds(skeleton_chars, chunked_chars):
    """Wall-clock estimate for masking one request.

    Two costs, not one. The skeleton POST carries every non-oversized message at
    full length and runs ONCE, sequentially — concurrency cannot help it, so it
    is charged at 1x. Only the chunk POSTs for oversized messages are spread
    across concurrent requests, so they are charged at the measured speedup.
    Charging the whole payload at the speedup (the earlier formula) let a
    history-heavy chat past the guard and straight into a 120 s wait that ended
    in a refusal anyway.
    """
    return skeleton_chars / PII_INLET_CHARS_PER_SECOND + chunked_chars / (
        PII_INLET_CHARS_PER_SECOND * PII_INLET_EFFECTIVE_SPEEDUP
    )


def max_maskable_chars():
    """Largest PASTE (no other history) that can be masked inside the
    wall-clock budget.

    Solves `estimated_masking_seconds(0, chunked_chars) = PII_INLET_TOTAL_BUDGET_S`
    for `chunked_chars` by dividing the budget by the cost of one chunked
    (concurrency-priced) character — `estimated_masking_seconds(0, 1)` — so the
    rate/speedup arithmetic lives in exactly one place in this module. It is
    NOT a general "total payload size" cap: a request that also carries a lot
    of ordinary (non-oversized) history pays the skeleton cost at 1x on top of
    this, so such a request can still legitimately exceed its own wall-clock
    budget while summing to fewer characters than this number. The router's
    guard therefore calls `estimated_masking_seconds` directly rather than
    comparing against this value; this function exists for the pure-paste case
    (and is what the test suite uses to build a boundary-sized payload).

    Expressed as a computation rather than a magic constant so that changing
    the budget, the measured rate, or the speedup moves the limit
    automatically instead of leaving a stale number behind — which is exactly
    how TRAU-513 ended up with a 50 000-char cap that meant 209 s on a
    sequential path.
    """
    return int(PII_INLET_TOTAL_BUDGET_S / estimated_masking_seconds(0, 1))


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
