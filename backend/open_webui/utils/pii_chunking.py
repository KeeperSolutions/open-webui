"""Shared sizing and splitting for PII masking calls (TRAU-543).

Deliberately dependency-free (no FastAPI, no aiohttp) so both `routers/` and
`utils/` can import it without a cycle, and so the splitter is unit-testable on
its own.

Every constant here is derived from ONE measurement (see
`pii_scripts/TRAU-543-PROMPT-PII-CHUNKING-PLAN.md`, Appendix A): the external
inlet processes text at a flat ~240 characters/second, independent of how much
you send it. Do not tune these by intuition — re-measure.
"""

import os


def _positive_int_env(name, default):
    """Read `name` from the environment as a positive int, falling back to
    `default` for anything unusable — unset, empty, non-numeric, zero or
    negative. A zero or negative budget would make `max_maskable_chars()`
    return 0 and refuse every prompt: fail-closed, so not dangerous, but
    indistinguishable from an outage and impossible to diagnose from the
    error message. Falling back to a working default is the kinder failure.
    """
    raw = os.getenv(name, '')
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default



def _positive_float_env(name, default):
    """Like `_positive_int_env`, for a rate rather than a count.

    Rejects NaN and infinity as well as zero and negatives: `float('nan')`
    passes a `> 0` test in neither direction and would silently make
    `max_maskable_chars()` nonsense, while `inf` would admit every document and
    then blow the deadline. Both fail closed, and both are impossible to
    diagnose from the error the user sees.
    """
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value != value or value in (float('inf'), float('-inf')):  # NaN / +-inf
        return default
    return value if value > 0 else default


# Characters per masking call. NOT a throughput knob: bigger chunks do not go
# faster (the rate is flat), they only eat the per-request margin — 6000 chars
# already measures ~25 s against a 30 s budget. 1800 chars is ~8 s.
PII_INLET_CHUNK_CHARS = 1800

# Sub-chunk POSTs in flight at once. Set to 4 because that is the EXACT
# concurrency the `PII_INLET_EFFECTIVE_SPEEDUP` measurement was taken at
# (2026-09-04 against the staging pipeline): four concurrent requests measured
# 8.6 / 13.6 / 18.3 / 25.2 s each — the FIFO signature of queueing behind one
# saturated CPU, not four requests actually running in parallel. Raising this
# constant is NOT "more throughput"; it is a timeout cliff. Each additional
# in-flight request adds roughly one more 8 s serial slot to the tail
# request's queueing delay, so at 10 the tail would wait ≈58 s against the
# 60 s `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ` — and a chunk that times out on the
# tail gets retried by `_mask_piece`, adding MORE load to the very bottleneck
# that caused the timeout. Do not raise this without re-measuring the
# speedup at the new concurrency first.
#
# That re-measurement has now been done, and the answer is: DO NOT BOTHER.
# 60 080 characters / 34 chunks against the live staging pipeline:
#
#     concurrency  4 -> 132.8s -> 452 chars/s
#     concurrency  8 -> 117.2s -> 513 chars/s
#     concurrency 12 -> 125.1s -> 480 chars/s
#     concurrency 16 -> 122.1s -> 492 chars/s
#
# Flat from 4 upward; the spread between 480, 492 and 513 is inside the noise of
# a single sample each. One pipeline instance saturates at ~490 chars/s and no
# amount of extra in-flight requests moves it — the ~2x over a lone request's
# 240 chars/s is network/vault/parse work overlapping NER, and that overlap is
# already fully exploited at 4. Raising this constant therefore buys nothing
# measurable while adding tail-latency risk against the 60s socket read,
# especially under contention from a second user masking at the same time.
# The ceiling is the pipeline's single NER thread on a single instance; it moves
# only when that service is allowed to scale out.
PII_INLET_CONCURRENCY = 4

# Wall-clock ceiling for masking ONE request, across all its chunks. Without it
# nothing bounds total time once the work is parallel — the per-request socket
# timeout only bounds a single POST. On expiry the request is refused
# (fail-closed), never forwarded.
#
# It is also the ONLY thing setting the maximum promptable size:
# `max_maskable_chars()` is arithmetic on this number, so raising it raises the
# cap and changes nothing else.
#
# It was 600s (10 min), and the comment here said that was "deliberately
# uncomfortable ... this large only because the staging pipeline is pinned to
# ONE instance ... Letting that service scale out is the real fix, after which
# this should come back down." That has now happened: the revision's maxScale
# and containerConcurrency were raised and the measured rate went from ~310 to
# 1 574 chars/s. So it comes back down.
#
# 240s buys MORE capacity than the old 600s did, not less, because the rate
# rose faster than the budget fell: the cap goes from ~259 000 to ~288 000
# characters while the worst-case wait drops from ten minutes to four.
#
# WARNING: this budget is only real if the transport survives it. The chat
# request stays open for the entire masking run, so a platform request timeout
# shorter than this turns a clean, actionable refusal into a dropped connection
# — strictly worse than the behaviour this ticket replaced. Cloud Run's default
# is 300s, which 240s fits inside with margin; 600s did not. Set the env var
# per environment instead of assuming the default fits.
PII_INLET_TOTAL_BUDGET_S = _positive_int_env('PII_INLET_TOTAL_BUDGET_S', 240)

# Per-chunk retries for transient failures (cold start, 5xx, dropped
# connection). Retrying one ~8 s chunk is far cheaper than failing a whole turn.
#
# Four, not three: the failures actually seen are Cloud Run bringing an instance
# up, and three attempts on the linear network-error schedule below spent every
# retry inside the first 1.5 s — long before a cold start finishes.
PII_INLET_CHUNK_RETRIES = 4

# First backoff for a chunk refused with a RETRYABLE STATUS (429 / transient
# 5xx), doubling per attempt: 2 s, 4 s, 8 s. Deliberately far longer than the
# schedule used for network errors, because these two failures mean different
# things. A dropped connection clears in milliseconds; a 429 from Cloud Run
# means the fleet has no free instance and one is being started, which takes
# seconds. Retrying that in half a second just re-fails against the same cold
# fleet and burns an attempt.
PII_INLET_RETRY_BACKOFF_S = 2.0

# Measured inlet throughput, characters per second (Appendix A). Everything
# below is arithmetic on this number; re-measure before changing it.
PII_INLET_CHARS_PER_SECOND = 240

# Safety margin applied when checking whether the SKELETON POST alone (every
# non-oversized message, sent as ONE sequential call with no chunking to fall
# back on) fits inside `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ`. Comparing against
# the raw timeout would admit a skeleton estimated at, say, 59.9s of a 60s
# budget — indistinguishable from a timeout given request/response overhead
# the flat characters/second rate does not model. 0.8 leaves real headroom;
# see the skeleton-fit guard in `routers/pipelines.py`.
PII_INLET_SKELETON_SAFETY_MARGIN = 0.8

# How much wall-clock concurrency ACTUALLY buys us, measured 2026-09-04 against
# the staging pipeline: one request alone 8.13 s, four concurrent 25.22 s total
# — a 1.29x speedup where perfect scale-out would be 4.0x. The service does not
# scale out; concurrent requests queue behind one saturated CPU. Deliberately
# NOT `PII_INLET_CONCURRENCY`: using the concurrency here would overstate
# capacity roughly eightfold and let prompts in that would then hit the deadline
# and be refused anyway. When the pipeline is allowed to scale out (Cloud Run
# instance limits, CPU allocation, GPU — infrastructure work outside this repo),
# this is the one number to raise.
#
# 1.29x was the BURST figure and it undercounts. Three later measurements, all
# sustained over enough chunks for the overlap to develop, agree closely:
#
#   live E2E, 160 980 chars / 90 chunks   -> 339.6 s -> 474 chars/s (1.98x)
#   user, 20 pages of Word (~64 000)      -> 120 s   -> 533 chars/s (2.22x)
#   user, 50 pages of Word (~160 000)     -> 300 s   -> 533 chars/s (2.22x)
#
# The burst probe only had four requests in flight, too few for one request's
# network/vault/parse work to overlap another's NER; small payloads show the same
# effect (a 4 096-char probe manages only 360 chars/s). All the numbers are real,
# they differ in how long the pipeline was kept busy.
#
# 1.8, not the measured ~2.0-2.2: every measurement was taken against an
# otherwise IDLE pipeline. It is a single instance serialized on one NER thread
# (`autoscaling.knative.dev/maxScale: 1`), so a second user masking at the same
# time comes straight off this number. The margin is deliberate — an optimistic
# value here admits a prompt that then dies on `PII_INLET_TOTAL_BUDGET_S`, which
# is the wait-then-refuse this ticket exists to remove. Being wrong low only
# costs cap headroom; being wrong high costs the user ten minutes and a refusal.
#
# RECALIBRATED 2026-09-08. The paragraph above ends "When the pipeline is allowed
# to scale out ... this is the one number to raise", and that is exactly what
# happened — the staging revision's maxScale and containerConcurrency were
# raised. Measured immediately after, end to end through `mask_sources_for_llm`:
#
#   one 50-page attachment, 167 460 chars -> 106.4 s -> 1 574 chars/s (6.6x)
#
# 5.0, not the measured 6.6: still one sample, still an otherwise idle pipeline,
# and a second user masking at the same time comes straight off it. 5.0 gives an
# effective 1 200 chars/s, which the same run beat by 30%.
#
# It is env-overridable because it is a claim about INFRASTRUCTURE, not about
# this repo: the number is wrong the moment someone rescales the service, and an
# operator must be able to correct it without a deploy. Being wrong high is now
# survivable in a way it was not before — the masking paths enforce a real
# `asyncio.wait_for` deadline, so an optimistic estimate ends in a bounded
# refusal rather than an unbounded wait.
#
# Sizing at 5.0: cap ~288 000 characters (~90 pages of Word), masked in ~183 s at
# the measured rate — inside the 240 s budget, so the guard and the deadline
# agree instead of contradicting each other.
PII_INLET_EFFECTIVE_SPEEDUP = _positive_float_env('PII_INLET_EFFECTIVE_SPEEDUP', 5.0)


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


def _token_before(text, index):
    """The whitespace-delimited token that ends at ``index`` (exclusive)."""
    j = index
    while j > 0 and not text[j - 1].isspace():
        j -= 1
    return text[j:index]


def _token_after(text, index):
    """The whitespace-delimited token that starts at ``index``."""
    j = index
    n = len(text)
    while j < n and not text[j].isspace():
        j += 1
    return text[index:j]


def _severs_spaced_entity(text, sep_index, split_at):
    """Whether breaking between the token ending at ``sep_index`` and the one
    starting at ``split_at`` would cut a SPACE-SEPARATED PII value in half.

    A single space is a terrible break point precisely where it matters most:
    `4111 1111 1111 1111`, `HR12 3456 7890 1234`, `+385 91 234 5678` and
    `Ivan Horvat` are all one entity written with spaces in it. Cut one and
    neither half is recognisable to the pipeline's recognisers, so BOTH halves
    reach the model unmasked — a leak, not a degradation (see
    `test_a_spaced_identifier_straddling_the_limit_is_not_severed`).

    Two cheap signals cover the multi-token entities Presidio actually emits:

      * both sides contain a digit -> a grouped identifier (card, IBAN, phone,
        account/reference number) is being split between its groups;
      * both sides start with a capital -> a PERSON / ORGANIZATION / LOCATION
        is being split between its words.

    Deliberately a heuristic on the SPLIT POINT rather than an attempt to
    recognise entities here: this module is dependency-free by design (no
    Presidio, no network), and moving a break a few words earlier costs
    nothing, while getting it wrong costs a leak. False positives only shift
    the break; they never drop or duplicate text.
    """
    left = _token_before(text, sep_index)
    right = _token_after(text, split_at)
    if not left or not right:
        return False  # a run of whitespace — never inside an entity
    if any(c.isdigit() for c in left) and any(c.isdigit() for c in right):
        return True
    if left[0].isupper() and right[0].isupper():
        return True
    return False


def _space_split_point(text, start, window_end):
    """Rightmost space break in ``(start, window_end)`` that does not sever a
    spaced entity, else the rightmost space of any kind, else -1.

    The fallback matters: when EVERY space in the window is unsafe (a window
    that is nothing but a list of names, say) the choice is between an unsafe
    space and a hard mid-token split, and the space is strictly better — it at
    least keeps single-token values whole. So this can only ever improve on
    the previous "take the last space" behaviour, never regress it.
    """
    fallback = -1
    idx = text.rfind(' ', start, window_end)
    while idx > start:
        split_at = idx + 1  # keep the separator with the left piece
        if fallback < 0:
            fallback = split_at
        if not _severs_spaced_entity(text, idx, split_at):
            return split_at
        idx = text.rfind(' ', start, idx)
    return fallback


def split_text_for_pii(text, max_chars=PII_INLET_CHUNK_CHARS):
    """Partition ``text`` into pieces no longer than ``max_chars`` whose
    concatenation is EXACTLY ``text`` (lossless — no dropped or duplicated
    chars). Breaks are taken at paragraph / line / space boundaries, in that
    order of preference, and a space break is additionally moved earlier when
    it would cut a space-separated PII value in two (see
    `_severs_spaced_entity`). Only when a window offers no usable break at all
    is a hard mid-run split used.

    Best-effort, not a guarantee: a window whose every space is unsafe still
    breaks at a space, and a single unbroken run longer than ``max_chars``
    still has to be cut somewhere. The common leak — a card, IBAN, phone
    number or full name landing on the window edge of an ordinary paste — is
    what this rules out.

    Returns a list of ``(start_offset, piece)`` tuples where ``start_offset`` is
    the piece's character offset within ``text``.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        # Guarded rather than assumed: a non-positive limit makes the loop
        # below non-terminating (`window_end` never advances past `i`, so it
        # appends an empty piece forever). The only production caller uses the
        # module constant, but the limit is an argument, so a future caller
        # must fail loudly instead of hanging a request thread.
        raise ValueError(f'max_chars must be a positive int, got {max_chars!r}')

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
        for sep in ('\n\n', '\n'):
            idx = text.rfind(sep, i, window_end)
            if idx > i:
                split_at = idx + len(sep)  # keep the separator with the piece
                break
        if split_at <= i:
            split_at = _space_split_point(text, i, window_end)
        if split_at <= i:
            split_at = window_end  # no boundary in window -> hard split
        pieces.append((i, text[i:split_at]))
        i = split_at
    return pieces
