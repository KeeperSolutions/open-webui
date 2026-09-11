"""Sizing arithmetic for PII masking.

Every number in `pii_chunking` is a claim about a REMOTE service's throughput,
so it goes stale the moment that service is rescaled — which is exactly what
happened: the model said 432 chars/s while staging, after its Cloud Run scaling
was raised, measured 1 574 (167 460 chars in 106.4s). These tests pin the
arithmetic and the two promises the numbers have to keep, not the values
themselves.
"""

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('stripe', MagicMock())

from open_webui.utils import pii_chunking as C


@pytest.mark.parametrize(
    'raw',
    ['', '   ', 'abc', '1.2.3', '0', '-1', '-0.5', 'nan', 'inf'],
)
def test_an_unusable_float_env_falls_back_to_the_default(monkeypatch, raw):
    """A zero, negative or NaN rate would make `max_maskable_chars()` return 0
    or explode and refuse every document — fail-closed, so not dangerous, but
    indistinguishable from an outage and impossible to diagnose from the error
    message. Falling back to a working default is the kinder failure."""
    monkeypatch.setenv('PII_TEST_RATE', raw)
    assert C._positive_float_env('PII_TEST_RATE', 1.8) == 1.8


def test_a_usable_float_env_is_honoured(monkeypatch):
    """The effective rate is a property of the deployment's infrastructure, not
    of this repo, so an operator has to be able to set it without a code change."""
    monkeypatch.setenv('PII_TEST_RATE', '5.5')
    assert C._positive_float_env('PII_TEST_RATE', 1.8) == 5.5


def test_the_cap_is_arithmetic_on_the_budget_not_a_magic_constant():
    """An earlier hand-written 50 000-char cap meant 209s once the
    path became sequential — a number that stopped tracking what it described.
    The cap has to move when the budget does."""
    one_char = C.estimated_masking_seconds(0, 1)
    assert C.max_maskable_chars() == int(C.PII_INLET_TOTAL_BUDGET_S / one_char)


def test_the_biggest_admissible_document_still_fits_the_budget():
    """Promise 1: the guard and the deadline must agree. If the cap admitted
    more than the budget can mask, the request would run to the deadline and be
    refused anyway — the wait-then-refuse this work exists to remove."""
    assert (
        C.estimated_masking_seconds(0, C.max_maskable_chars()) <= C.PII_INLET_TOTAL_BUDGET_S
    )


def test_the_measured_document_is_admitted():
    """Promise 2: the 167 460-char attachment measured end to end on staging
    must not be refused. It is the largest thing we have actually watched the
    pipeline mask, so a cap below it would be refusing known-good work."""
    assert C.max_maskable_chars() >= 167_460


def test_the_worst_case_wait_fits_a_default_platform_request_timeout():
    """The budget is only real if the transport survives it: the chat request
    is held open for the whole masking run, so a platform timeout shorter than
    the budget turns a clean refusal into a dropped connection — strictly worse
    than the behaviour this replaced. Cloud Run's default is 300s."""
    assert C.PII_INLET_TOTAL_BUDGET_S <= 300
