"""Tests for the PII masking size limits in `pii_chunking`.

The throughput constants describe an external service, so their values change
when that service is rescaled. These tests check how the limits are calculated
from the constants and the guarantees the limits must keep, not the constant
values themselves.
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
    """An unusable rate falls back to the default. A zero, negative or NaN rate
    would make `max_maskable_chars()` return 0 or fail, so every document would
    be refused with an error that does not point at the misconfigured variable."""
    monkeypatch.setenv('PII_TEST_RATE', raw)
    assert C._positive_float_env('PII_TEST_RATE', 1.8) == 1.8


def test_a_usable_float_env_is_honoured(monkeypatch):
    """A valid rate from the environment is used. The rate depends on the
    deployment's infrastructure, so operators set it without a code change."""
    monkeypatch.setenv('PII_TEST_RATE', '5.5')
    assert C._positive_float_env('PII_TEST_RATE', 1.8) == 5.5


def test_the_cap_is_arithmetic_on_the_budget_not_a_magic_constant():
    """The character limit is the time budget in seconds divided by the
    estimated seconds to mask one character. Because it is calculated rather
    than hard-coded, changing the budget or the throughput settings changes the
    limit too."""
    one_char = C.estimated_masking_seconds(0, 1)
    assert C.max_maskable_chars() == int(C.PII_INLET_TOTAL_BUDGET_S / one_char)


def test_the_biggest_admissible_document_still_fits_the_budget():
    """The largest document the character limit admits can be masked within the
    time budget. Otherwise a document could pass the size check, wait for the
    whole budget, and then be refused when the deadline expires."""
    assert (
        C.estimated_masking_seconds(0, C.max_maskable_chars()) <= C.PII_INLET_TOTAL_BUDGET_S
    )


def test_the_measured_document_is_admitted():
    """A 167 460-character document is admitted. It is the largest document the
    pipeline is known to mask successfully, so a lower limit would refuse
    documents that work."""
    assert C.max_maskable_chars() >= 167_460


def test_the_worst_case_wait_fits_a_default_platform_request_timeout():
    """The time budget is at most 300 seconds, the default Cloud Run request
    timeout. The chat request stays open while masking runs, so with a longer
    budget the platform could close the connection before the user receives
    either the masked result or the refusal message."""
    assert C.PII_INLET_TOTAL_BUDGET_S <= 300
