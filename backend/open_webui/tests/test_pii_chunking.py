"""Unit tests for the shared PII chunk splitter (TRAU-543)."""

import pytest

from open_webui.utils.pii_chunking import (
    PII_INLET_CHUNK_CHARS,
    split_text_for_pii,
)


def test_short_text_is_a_single_piece_at_offset_zero():
    assert split_text_for_pii('kratko') == [(0, 'kratko')]


def test_non_string_input_is_passed_through_untouched():
    assert split_text_for_pii(None) == [(0, None)]


def test_partition_is_lossless_and_offsets_are_exact():
    """The concatenation must reproduce the input EXACTLY: a dropped or
    duplicated character silently corrupts what the LLM reads, and a wrong
    offset makes every downstream detection span point at the wrong text."""
    # Use non-periodic content so offset errors cannot coincidentally pass.
    # Each line has a unique counter prefix, preventing periodicity masking.
    text = ''.join(f'{i}: Ugovorna strana Ivan Horvat, OIB 12345678903.\n\n' for i in range(400))
    pieces = split_text_for_pii(text, max_chars=1800)
    assert ''.join(p for _, p in pieces) == text
    for start, piece in pieces:
        assert text[start : start + len(piece)] == piece


def test_no_piece_exceeds_the_limit():
    text = 'riječ ' * 5000
    assert all(len(p) <= 1800 for _, p in split_text_for_pii(text, max_chars=1800))


def test_breaks_prefer_paragraph_then_line_then_space():
    text = 'a' * 1000 + '\n\n' + 'b' * 1000 + '\n' + 'c' * 1000 + ' ' + 'd' * 1000
    pieces = [p for _, p in split_text_for_pii(text, max_chars=1800)]
    # A break inside a run of identical characters would mean the separator
    # search failed; every piece must therefore end on a separator (or be last).
    assert all(p.endswith(('\n\n', '\n', ' ')) for p in pieces[:-1])


def test_an_unbroken_run_longer_than_the_limit_is_hard_split():
    """No boundary exists, so the splitter must still make progress rather
    than loop forever or emit an over-long piece."""
    text = 'x' * 5000
    pieces = split_text_for_pii(text, max_chars=1800)
    assert ''.join(p for _, p in pieces) == text
    assert all(len(p) <= 1800 for _, p in pieces)


def test_an_entity_is_never_severed_by_a_break():
    """A PII value split across two pieces is detected in neither half — a LEAK,
    not a degradation. Padded so the IBAN sits exactly at the window edge."""
    iban = 'HR1210010051863000160'
    text = 'a' * 1795 + ' ' + iban + ' rest'
    pieces = [p for _, p in split_text_for_pii(text, max_chars=1800)]
    assert any(iban in p for p in pieces)


def test_budgets_are_consistent_with_the_measured_throughput():
    """Guards the numbers in Appendix A: at the measured rate a chunk must
    finish well inside the 60 s socket-read budget."""
    from open_webui.utils.pii_chunking import (
        PII_INLET_CHARS_PER_SECOND,
        PII_INLET_TOTAL_BUDGET_S,
    )

    assert PII_INLET_CHUNK_CHARS / PII_INLET_CHARS_PER_SECOND < 30
    assert PII_INLET_TOTAL_BUDGET_S >= 60
