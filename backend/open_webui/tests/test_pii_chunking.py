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


@pytest.mark.parametrize(
    'value',
    [
        '4111 1111 1111 1111',  # credit card written in groups of four
        'HR12 3456 7890 1234 567',  # IBAN in groups
        '+385 91 234 5678',  # phone number
        'Ivan Horvat',  # PERSON — two capitalised words
    ],
)
def test_a_spaced_identifier_straddling_the_limit_is_not_severed(value):
    """A SPACE-separated PII value must survive the break whole.

    `test_an_entity_is_never_severed_by_a_break` only ever covered values with
    no internal space, so it passed while the splitter happily cut
    `4111 1111 1111 1111` into `...4111 ` + `1111 1111 1111`. Neither half is
    a credit card to any recogniser, so BOTH halves reached the model
    unmasked. Swept across the window edge because only a few of the ~20
    offsets put a space of the value exactly at the limit.
    """
    for pad in range(1780, 1800):
        text = 'a' * pad + ' ' + value + ' ostatak recenice koji ide dalje'
        pieces = split_text_for_pii(text, max_chars=1800)
        assert ''.join(p for _, p in pieces) == text  # still lossless
        assert any(value in p for _, p in pieces), f'severed at pad={pad}'


def test_an_all_unsafe_window_still_breaks_at_a_space():
    """When every space in the window would sever an entity there is nothing
    safe to pick, and a space is still strictly better than a hard mid-token
    split — the fallback must not regress into cutting words in half."""
    text = 'Ivan Horvat ' * 400
    pieces = [p for _, p in split_text_for_pii(text, max_chars=1800)]
    assert ''.join(pieces) == text
    assert all(p.endswith(' ') for p in pieces[:-1])


@pytest.mark.parametrize('bad', [0, -1, True, 1.5, '1800', None])
def test_a_non_positive_chunk_limit_is_rejected_instead_of_looping_forever(bad):
    """With `max_chars <= 0` the window never advances: the splitter appended
    an empty piece and spun forever, hanging the request thread. The limit is
    an argument, so it is validated rather than assumed."""
    with pytest.raises(ValueError):
        split_text_for_pii('x' * 5000, max_chars=bad)


def test_budgets_are_consistent_with_the_measured_throughput():
    """Guards the measured sizing numbers: at the measured rate a chunk must
    finish well inside the 60 s socket-read budget."""
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
    """`PII_INLET_TOTAL_BUDGET_S` is env-tunable because the honest value
    depends on the deployment's own request timeout. A typo must not silently
    refuse every prompt: zero or negative would make `max_maskable_chars()`
    return 0, which is fail-closed but indistinguishable from an outage."""
    from open_webui.utils.pii_chunking import _positive_int_env

    if raw is None:
        monkeypatch.delenv('PII_INLET_TOTAL_BUDGET_S', raising=False)
    else:
        monkeypatch.setenv('PII_INLET_TOTAL_BUDGET_S', raw)
    assert _positive_int_env('PII_INLET_TOTAL_BUDGET_S', 600) == expected
