"""Unit tests for the shared PII chunk splitter."""

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
    """Joining the pieces reproduces the input exactly, and each offset is the
    position of its piece in the input. A lost or duplicated character changes
    the text the LLM reads, and a wrong offset puts detected entity spans on the
    wrong text."""
    # Each line starts with a unique counter so the text does not repeat. In
    # repeating text a wrong offset can still point at identical characters.
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
    # Every piece except the last must end on a separator. A piece that ends
    # inside a run of identical letters means no separator was found.
    assert all(p.endswith(('\n\n', '\n', ' ')) for p in pieces[:-1])


def test_an_unbroken_run_longer_than_the_limit_is_hard_split():
    """Text with no separator is cut at the limit. The splitter must not loop
    forever or return a piece longer than the limit."""
    text = 'x' * 5000
    pieces = split_text_for_pii(text, max_chars=1800)
    assert ''.join(p for _, p in pieces) == text
    assert all(len(p) <= 1800 for _, p in pieces)


def test_an_entity_is_never_severed_by_a_break():
    """A value that crosses the limit stays whole in one piece. If a PII value
    were split across two pieces, neither half would be detected and the value
    would reach the LLM unmasked. The padding places the IBAN across the edge
    of the 1800-character window."""
    iban = 'HR1210010051863000160'
    text = 'a' * 1795 + ' ' + iban + ' rest'
    pieces = [p for _, p in split_text_for_pii(text, max_chars=1800)]
    assert any(iban in p for p in pieces)


@pytest.mark.parametrize(
    'value',
    [
        '4111 1111 1111 1111',  # credit card written in groups of four
        'HR12 3456 7890 1234 567',  # IBAN in groups
        '+385 91 234 5678',  # phone number
        'Ivan Horvat',  # person name: two capitalised words
    ],
)
def test_a_spaced_identifier_straddling_the_limit_is_not_severed(value):
    """A PII value that contains spaces stays whole in one piece.

    A break at one of the value's internal spaces, such as `...4111 ` +
    `1111 1111 1111`, leaves two halves that no recogniser detects, so both
    reach the model unmasked. The test tries every padding from 1780 to 1799
    characters because only a few of those offsets put one of the value's
    spaces exactly at the limit.
    """
    for pad in range(1780, 1800):
        text = 'a' * pad + ' ' + value + ' ostatak recenice koji ide dalje'
        pieces = split_text_for_pii(text, max_chars=1800)
        assert ''.join(p for _, p in pieces) == text  # still lossless
        assert any(value in p for _, p in pieces), f'severed at pad={pad}'


def test_an_all_unsafe_window_still_breaks_at_a_space():
    """If every space in the window would split an entity, the splitter still
    breaks at a space instead of cutting a word in half."""
    text = 'Ivan Horvat ' * 400
    pieces = [p for _, p in split_text_for_pii(text, max_chars=1800)]
    assert ''.join(pieces) == text
    assert all(p.endswith(' ') for p in pieces[:-1])


@pytest.mark.parametrize('bad', [0, -1, True, 1.5, '1800', None])
def test_a_non_positive_chunk_limit_is_rejected_instead_of_looping_forever(bad):
    """A `max_chars` that is not a positive int raises `ValueError`. With
    `max_chars <= 0` the window never advances, so the splitter would add empty
    pieces forever and hang the request thread."""
    with pytest.raises(ValueError):
        split_text_for_pii('x' * 5000, max_chars=bad)


def test_budgets_are_consistent_with_the_measured_throughput():
    """At `PII_INLET_CHARS_PER_SECOND`, one chunk takes under 30 seconds, well
    inside the 60-second socket-read timeout, and the total budget is at least
    60 seconds."""
    from open_webui.utils.pii_chunking import (
        PII_INLET_CHARS_PER_SECOND,
        PII_INLET_TOTAL_BUDGET_S,
    )

    assert PII_INLET_CHUNK_CHARS / PII_INLET_CHARS_PER_SECOND < 30
    assert PII_INLET_TOTAL_BUDGET_S >= 60


@pytest.mark.parametrize(
    'raw, expected',
    [
        (None, 600),  # unset
        ('', 600),  # set but empty
        ('not-a-number', 600),
        ('0', 600),  # would make max_maskable_chars() 0 and refuse everything
        ('-30', 600),
        ('900', 900),
    ],
)
def test_the_budget_env_override_falls_back_to_the_default_for_anything_unusable(monkeypatch, raw, expected):
    """`PII_INLET_TOTAL_BUDGET_S` can be set per deployment because the right
    value depends on that deployment's request timeout. An unusable value falls
    back to the default: zero or negative would make `max_maskable_chars()`
    return 0 and refuse every prompt, which fails closed but looks like an
    outage."""
    from open_webui.utils.pii_chunking import _positive_int_env

    if raw is None:
        monkeypatch.delenv('PII_INLET_TOTAL_BUDGET_S', raising=False)
    else:
        monkeypatch.setenv('PII_INLET_TOTAL_BUDGET_S', raw)
    assert _positive_int_env('PII_INLET_TOTAL_BUDGET_S', 600) == expected
