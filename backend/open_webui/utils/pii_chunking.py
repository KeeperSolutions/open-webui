"""Shared sizing and splitting for PII masking calls.

This module imports neither FastAPI nor aiohttp, so both `routers/` and
`utils/` can import it without a circular import, and the splitter can be
unit-tested on its own.

The sizing constants are based on the measured throughput of the external PII
pipeline. Re-measure before changing them.
"""

import os


def _positive_int_env(name, default):
    """Read `name` from the environment as a positive int.

    Returns `default` when the value is unset, empty, non-numeric, zero or
    negative. A zero or negative budget would make `max_maskable_chars()`
    return 0 and refuse every prompt. That is fail-closed, but it looks like
    an outage and the error message does not explain it, so an invalid value
    falls back to the default instead.
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
    """Like `_positive_int_env`, for a float value.

    Also rejects NaN and infinity. NaN is neither greater nor less than zero
    and breaks the arithmetic in `max_maskable_chars()`; infinity would admit
    every document, which would then run out of time. Both would still fail
    closed, but with refusals the user's error message does not explain.
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


# Maximum characters per masking request. Chunk size does not change
# throughput, because the pipeline's rate per character is constant; larger
# chunks only make each request take longer. At about 240 characters per
# second, a 1800-character chunk takes about 8 s.
PII_INLET_CHUNK_CHARS = 1800

# Number of chunk requests sent to the pipeline at the same time. Measured
# throughput did not improve between 4 and 16: requests queue behind the
# pipeline's single NER thread. More requests only lengthen the queue, so the
# last one can exceed `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ` (default 60 s), and
# its retry adds load to the same queue. Re-measure before raising it.
PII_INLET_CONCURRENCY = 4

# Maximum wall-clock time, in seconds, to mask one request across all of its
# chunks. When it expires the request is refused (fail-closed) and never
# forwarded unmasked. The socket timeout only limits a single POST, so this is
# the only limit on the total time.
#
# It also sets the largest maskable paste: `max_maskable_chars()` is calculated
# from it.
#
# It must stay below the platform request timeout (Cloud Run default: 300 s).
# The chat request stays open while masking runs, so a shorter platform timeout
# drops the connection instead of returning a clear refusal. The default of
# 240 s fits inside the Cloud Run default with margin. Override it per
# environment with the `PII_INLET_TOTAL_BUDGET_S` env var.
PII_INLET_TOTAL_BUDGET_S = _positive_int_env('PII_INLET_TOTAL_BUDGET_S', 240)

# Maximum attempts per chunk, including the first, for transient failures
# (cold start, retryable HTTP status, dropped connection). Retrying one chunk
# is much cheaper than failing the whole request. Four attempts spread the
# network-error retries (0.5 s, 1 s, 1.5 s) over 3 s rather than 1.5 s, which
# matters because the failures seen in practice are Cloud Run instances
# starting up.
PII_INLET_CHUNK_RETRIES = 4

# First delay, in seconds, before retrying a chunk that received a retryable
# HTTP status (429 or a transient 5xx). It doubles on each attempt: 2 s, 4 s,
# 8 s. It is longer than the network-error delay because a 429 from Cloud Run
# means a new instance is being started, which takes seconds, while a dropped
# connection usually clears in milliseconds.
PII_INLET_RETRY_BACKOFF_S = 2.0

# Masking throughput of a single request, in characters per second, measured
# against the pipeline. The time estimates below are calculated from it.
PII_INLET_CHARS_PER_SECOND = 240

# Fraction of `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ` that the estimated time of the
# skeleton request may use. The skeleton request is the single call that
# carries the messages that are not chunked, so it cannot fall back to
# chunking. The characters-per-second estimate ignores request and response
# overhead, so an estimate close to the full timeout would likely time out.
# The check is in `routers/pipelines.py`.
PII_INLET_SKELETON_SAFETY_MARGIN = 0.8

# How many times faster chunked text is masked than a single request would
# mask it, because chunks are sent concurrently. Chunked text is estimated at
# `PII_INLET_CHARS_PER_SECOND` times this value (5.0 gives 1 200 characters per
# second). It is measured, not derived from `PII_INLET_CONCURRENCY`.
#
# The default of 5.0 is below the measured speedup of about 6.6, because that
# was measured on an otherwise idle pipeline and other users masking at the
# same time reduce it. A value that is too high admits prompts that then hit
# `PII_INLET_TOTAL_BUDGET_S` and are refused after the full wait; a value that
# is too low only lowers the size limit.
#
# The right value depends on how the pipeline service is scaled, not on this
# code, so it can be overridden with the `PII_INLET_EFFECTIVE_SPEEDUP` env var
# without a deploy. At 5.0 with a 240 s budget, the paste limit is about
# 288 000 characters.
PII_INLET_EFFECTIVE_SPEEDUP = _positive_float_env('PII_INLET_EFFECTIVE_SPEEDUP', 5.0)


def estimated_masking_seconds(skeleton_chars, chunked_chars):
    """Estimated wall-clock seconds to mask one request.

    The skeleton request (the messages that are not chunked) is one sequential
    call, so its characters are charged at `PII_INLET_CHARS_PER_SECOND`. Only
    the chunks of oversized messages are sent concurrently, so only they get
    the `PII_INLET_EFFECTIVE_SPEEDUP` rate. Charging everything at the faster
    rate would underestimate chats with long history, letting them pass the
    size check and then be refused when the time budget runs out.
    """
    return skeleton_chars / PII_INLET_CHARS_PER_SECOND + chunked_chars / (
        PII_INLET_CHARS_PER_SECOND * PII_INLET_EFFECTIVE_SPEEDUP
    )


def max_maskable_chars():
    """Largest single paste, with no other history, that fits the time budget.

    Divides `PII_INLET_TOTAL_BUDGET_S` by the estimated time for one chunked
    character, `estimated_masking_seconds(0, 1)`, so the rate arithmetic stays
    in `estimated_masking_seconds`. This is not a limit on total request size:
    messages that are not chunked cost more time per character, so a request
    with a lot of history can exceed the budget with fewer characters than
    this. The router's check therefore calls `estimated_masking_seconds`
    directly. This function covers the paste-only case, and tests use it to
    build a payload at the limit.

    The limit is calculated from the time budget, not hard-coded, so changing
    the budget or the throughput settings changes the limit too.
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


def _is_identifier_group(token):
    """Whether ``token`` looks like one group of a grouped identifier: it
    contains a digit (`1234`, `GB82`) or is all upper case (`WEST`)."""
    return any(c.isdigit() for c in token) or token.isupper()


def _severs_spaced_entity(text, sep_index, split_at):
    """Whether breaking between the token ending at ``sep_index`` and the token
    starting at ``split_at`` would split a PII value that contains spaces.

    Values such as `4111 1111 1111 1111`, `GB82 WEST 1234 5698 7654 32`,
    `+44 20 7946 0958` and `John Doe` are single entities written with
    spaces. If one is split across two chunks, the pipeline recognises neither
    half, and both halves reach the model unmasked (see
    `test_a_spaced_identifier_straddling_the_limit_is_not_severed`).

    Two checks cover the multi-token entities Presidio detects:

      * both tokens are identifier groups, meaning each contains a digit or is
        all upper case: a grouped identifier (card, IBAN, phone, account or
        reference number) would be split between its groups. All upper case
        covers letters-only IBAN groups such as the bank code `WEST`;
      * both tokens start with a capital letter: a PERSON, ORGANIZATION or
        LOCATION would be split between its words.

    This checks the split point instead of recognising entities, because this
    module has no Presidio or network dependency. A false positive only moves
    the break earlier; it never drops or duplicates text.
    """
    left = _token_before(text, sep_index)
    right = _token_after(text, split_at)
    if not left or not right:
        return False  # whitespace run, never inside an entity
    if _is_identifier_group(left) and _is_identifier_group(right):
        return True
    if left[0].isupper() and right[0].isupper():
        return True
    return False


def _space_split_point(text, start, window_end):
    """Rightmost space break in ``(start, window_end)`` that does not split a
    spaced entity, else the rightmost space break of any kind, else -1.

    When every space in the window is unsafe (for example, a list of names), a
    space break is still better than a hard mid-token split, because it keeps
    single-token values whole.
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
    """Split ``text`` into pieces of at most ``max_chars`` characters.

    Joining the pieces gives back exactly ``text``: no characters are dropped
    or duplicated. Breaks are taken at paragraph, line, then space boundaries,
    in that order of preference. A space break is moved earlier when it would
    split a PII value that contains spaces (see `_severs_spaced_entity`). A
    hard split inside a run of text is used only when the window has no
    usable break.

    This is best-effort: a window whose every space is unsafe still breaks at
    a space, and a run without whitespace longer than ``max_chars`` must be
    cut somewhere. It does prevent the common case of a card number, IBAN,
    phone number or full name falling on a chunk boundary in ordinary text.

    Returns a list of ``(start_offset, piece)`` tuples where ``start_offset`` is
    the piece's character offset within ``text``.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        # A non-positive limit would make the loop below run forever, because
        # `window_end` never moves past `i` and an empty piece is appended on
        # every pass. The only production caller passes the module constant,
        # but the limit is an argument, so an invalid value raises instead of
        # hanging the request.
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
