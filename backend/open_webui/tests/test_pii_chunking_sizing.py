"""Tests for the PII masking size limits in `pii_chunking`.

The throughput constants describe an external service, so their values change
when that service is rescaled. These tests check how the limits are calculated
from the constants and the guarantees the limits must keep, not the constant
values themselves.
"""

import importlib
import logging
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('stripe', MagicMock())

from open_webui.utils import pii_chunking as C  # noqa: E402

TUNABLE_SETTINGS = (
    'PII_INLET_CHUNK_CHARS',
    'PII_INLET_CONCURRENCY',
    'PII_INLET_TOTAL_BUDGET_S',
    'PII_INLET_CHUNK_RETRIES',
    'PII_INLET_RETRY_BACKOFF_S',
    'PII_INLET_CHARS_PER_SECOND',
    'PII_INLET_EFFECTIVE_SPEEDUP',
)


@pytest.fixture
def reload_chunking(monkeypatch):
    """Return a function that reloads `pii_chunking` so its settings are read
    from the current environment. The settings start unset. Afterwards the
    environment is restored and the module reloaded, so later tests see the
    settings the process started with."""
    for name in TUNABLE_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    yield lambda: importlib.reload(C)
    monkeypatch.undo()
    importlib.reload(C)


def test_an_int_env_outside_its_range_falls_back_to_the_default(monkeypatch):
    """A value above `maximum` or below `minimum` falls back to the default."""
    monkeypatch.setenv('PII_TEST_COUNT', '17')
    assert C._positive_int_env('PII_TEST_COUNT', 4, maximum=16) == 4
    monkeypatch.setenv('PII_TEST_COUNT', '199')
    assert C._positive_int_env('PII_TEST_COUNT', 1800, minimum=200) == 1800


def test_an_int_env_at_its_range_limits_is_honoured(monkeypatch):
    monkeypatch.setenv('PII_TEST_COUNT', '16')
    assert C._positive_int_env('PII_TEST_COUNT', 4, maximum=16) == 16
    monkeypatch.setenv('PII_TEST_COUNT', '200')
    assert C._positive_int_env('PII_TEST_COUNT', 1800, minimum=200) == 200


def test_a_float_env_above_its_maximum_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv('PII_TEST_RATE', '30.5')
    assert C._positive_float_env('PII_TEST_RATE', 2.0, maximum=30) == 2.0
    monkeypatch.setenv('PII_TEST_RATE', '30')
    assert C._positive_float_env('PII_TEST_RATE', 2.0, maximum=30) == 30.0


def test_a_rejected_env_value_is_logged_with_the_variable_name(monkeypatch, caplog):
    """A rejected value is logged as a warning that names the variable, so a
    misconfiguration is visible in the logs instead of silently ignored."""
    monkeypatch.setenv('PII_TEST_COUNT', '0')
    with caplog.at_level(logging.WARNING, logger=C.log.name):
        C._positive_int_env('PII_TEST_COUNT', 4)
    assert 'PII_TEST_COUNT' in caplog.text


@pytest.mark.parametrize(
    'name, raw, expected',
    [
        ('PII_INLET_CHUNK_CHARS', '1200', 1200),
        ('PII_INLET_CONCURRENCY', '8', 8),
        ('PII_INLET_CHUNK_RETRIES', '6', 6),
        ('PII_INLET_RETRY_BACKOFF_S', '0.5', 0.5),
        ('PII_INLET_CHARS_PER_SECOND', '600', 600),
    ],
)
def test_tunable_settings_are_read_from_the_environment(monkeypatch, reload_chunking, name, raw, expected):
    """Settings that depend on how the pipeline service is deployed can be
    changed with an env var, without a code change."""
    monkeypatch.setenv(name, raw)
    assert getattr(reload_chunking(), name) == expected


@pytest.mark.parametrize(
    'name, raw',
    [
        ('PII_INLET_CHUNK_CHARS', '0'),
        ('PII_INLET_CHUNK_CHARS', '20000'),
        ('PII_INLET_CONCURRENCY', '0'),
        ('PII_INLET_CONCURRENCY', '64'),
        ('PII_INLET_CHUNK_RETRIES', '0'),
        ('PII_INLET_CHUNK_RETRIES', '50'),
        ('PII_INLET_RETRY_BACKOFF_S', '120'),
        ('PII_INLET_CHARS_PER_SECOND', '-1'),
    ],
)
def test_an_out_of_range_tunable_setting_keeps_the_default(monkeypatch, reload_chunking, name, raw):
    """An out-of-range value would stall or refuse every masked message (for
    example zero concurrency never sends a chunk), so it is ignored."""
    default = getattr(reload_chunking(), name)
    monkeypatch.setenv(name, raw)
    assert getattr(reload_chunking(), name) == default



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
