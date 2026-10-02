"""
Tests for the global PII_ACTIVE default in _resolve_pii_masking_decision
(utils/middleware.py). Mirrors getPiiMaskingDefault() on the frontend: when a
request carries no explicit features.pii_masking choice at all (request_pii is
None - a non-browser caller that omits the field; the chat web UI always sends
an explicit value), PII_ACTIVE decides. A team/group policy always wins first,
and an explicit client choice (True or False) is always respected as-is.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# stripe is an optional billing dependency not installed in the test environment.
sys.modules.setdefault("stripe", MagicMock())

from open_webui.utils.middleware import _resolve_pii_masking_decision


def _run(coro):
    return asyncio.run(coro)


def _make_user():
    return SimpleNamespace(id="user-1", email="t@example.com", name="Test User", role="user")


def _resolve(features, *, policy_enforced, pii_active):
    request = MagicMock()
    user = _make_user()
    with patch(
        "open_webui.utils.middleware.resolve_pii_masking_enforced",
        AsyncMock(return_value=policy_enforced),
    ), patch("open_webui.utils.middleware.PII_ACTIVE", pii_active):
        return _run(_resolve_pii_masking_decision(request, user, features))


class TestPiiActiveFallback:
    def test_no_explicit_choice_falls_back_to_pii_active_true(self):
        policy_enforced, pii_expected = _resolve({}, policy_enforced=False, pii_active=True)
        assert policy_enforced is False
        assert pii_expected is True

    def test_no_explicit_choice_falls_back_to_pii_active_false(self):
        policy_enforced, pii_expected = _resolve({}, policy_enforced=False, pii_active=False)
        assert pii_expected is False

    def test_non_dict_features_falls_back_to_pii_active(self):
        """features=None (no features payload at all) must behave like an
        unspecified choice, not like an explicit True."""
        policy_enforced, pii_expected = _resolve(None, policy_enforced=False, pii_active=False)
        assert pii_expected is False

    def test_explicit_true_wins_over_pii_active_false(self):
        policy_enforced, pii_expected = _resolve(
            {"pii_masking": True}, policy_enforced=False, pii_active=False
        )
        assert pii_expected is True

    def test_explicit_false_wins_over_pii_active_true(self):
        policy_enforced, pii_expected = _resolve(
            {"pii_masking": False}, policy_enforced=False, pii_active=True
        )
        assert pii_expected is False

    def test_policy_enforced_wins_over_pii_active_false(self):
        policy_enforced, pii_expected = _resolve({}, policy_enforced=True, pii_active=False)
        assert policy_enforced is True
        assert pii_expected is True

    def test_policy_enforced_wins_over_explicit_false(self):
        """A mandated policy overrides even an explicit opt-out, same as before
        this change - PII_ACTIVE must not weaken that guarantee."""
        policy_enforced, pii_expected = _resolve(
            {"pii_masking": False}, policy_enforced=True, pii_active=False
        )
        assert pii_expected is True
