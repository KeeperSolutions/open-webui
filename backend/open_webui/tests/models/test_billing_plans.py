"""Tests for models/billing_plans.py — plan tier constants."""
import pytest
from open_webui.models.billing_plans import (
    PLAN_TIER_INTERNAL,
    PLAN_TIER_TRIAL,
    PLAN_TIER_PRO,
    PLAN_TIER_PREMIUM,
    PLAN_TIER_TEAM,
    PLAN_TIER_TEAM_MEMBER,
    CREDITS_TIERS,
    PLAN_CREDITS,
)


class TestPlanTierValues:
    def test_internal(self):
        assert PLAN_TIER_INTERNAL == "internal"

    def test_trial(self):
        assert PLAN_TIER_TRIAL == "trial"

    def test_pro(self):
        assert PLAN_TIER_PRO == "pro"

    def test_premium(self):
        assert PLAN_TIER_PREMIUM == "premium"

    def test_team(self):
        assert PLAN_TIER_TEAM == "team"

    def test_team_member(self):
        assert PLAN_TIER_TEAM_MEMBER == "team_member"

    def test_no_paid_string_exists(self):
        """The legacy 'paid' tier must not appear anywhere in the constants."""
        all_tiers = {
            PLAN_TIER_INTERNAL, PLAN_TIER_TRIAL, PLAN_TIER_PRO,
            PLAN_TIER_PREMIUM, PLAN_TIER_TEAM, PLAN_TIER_TEAM_MEMBER,
        }
        assert "paid" not in all_tiers


class TestCreditsTiers:
    def test_trial_in_credits_tiers(self):
        assert PLAN_TIER_TRIAL in CREDITS_TIERS

    def test_pro_in_credits_tiers(self):
        assert PLAN_TIER_PRO in CREDITS_TIERS

    def test_premium_in_credits_tiers(self):
        assert PLAN_TIER_PREMIUM in CREDITS_TIERS

    def test_internal_not_in_credits_tiers(self):
        assert PLAN_TIER_INTERNAL not in CREDITS_TIERS

    def test_team_not_in_credits_tiers(self):
        assert PLAN_TIER_TEAM not in CREDITS_TIERS

    def test_team_member_not_in_credits_tiers(self):
        assert PLAN_TIER_TEAM_MEMBER not in CREDITS_TIERS

    def test_paid_not_in_credits_tiers(self):
        assert "paid" not in CREDITS_TIERS

    def test_is_frozenset(self):
        assert isinstance(CREDITS_TIERS, frozenset)


class TestPlanCredits:
    def test_pro_credits(self):
        assert PLAN_CREDITS[PLAN_TIER_PRO] == 1300

    def test_premium_credits(self):
        assert PLAN_CREDITS[PLAN_TIER_PREMIUM] == 3800

    def test_no_paid_key(self):
        assert "paid" not in PLAN_CREDITS

    def test_all_keys_are_valid_tiers(self):
        valid = {PLAN_TIER_TRIAL, PLAN_TIER_PRO, PLAN_TIER_PREMIUM}
        for key in PLAN_CREDITS:
            assert key in valid, f"Unexpected key in PLAN_CREDITS: {key!r}"
