"""Tests for the poll overlap: how far before the newest stored observation each poll starts reading.

The poller loop runs against a fake Langfuse and a fake ledger on a simulated clock. A fake
observation becomes visible some time after its startTime, as a delayed ingestion would.
"""

import asyncio
import datetime
from unittest.mock import MagicMock, patch

import pytest

import open_webui.tasks.billing as billing

T0 = 1_800_000_000
MINUTE = 60


class StopLoop(Exception):
    """Raised by the fake sleep to end the poller loop after the planned number of polls."""


class FakeLedger:
    """The ledger calls the poller and the sync make, backed by a dict of observation id to observed_at."""

    def __init__(self, rows: dict):
        self.rows = dict(rows)

    def get_max_observed_at(self):
        return max(self.rows.values()) if self.rows else None

    def bulk_insert_ignore(self, rows):
        new = {r["langfuse_observation_id"] for r in rows if r["langfuse_observation_id"] not in self.rows}
        for r in rows:
            self.rows.setdefault(r["langfuse_observation_id"], r["observed_at"])
        return new

    def bulk_upsert_costs(self, rows):
        return set()

    def bulk_upsert_user_ids(self, rows):
        return 0

    def get_cost_eur_for_users_current_month(self, user_ids):
        return {}

    def get_models_with_recent_priced_rows(self, model_names, since_ts):
        return []


def _iso(epoch: int) -> str:
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _row(obs_id: str, start: int) -> dict:
    return {
        "id": obs_id,
        "userId": "user@example.test",
        "model": "model-a",
        "startTime": _iso(start),
        "usage": {"input": 10, "output": 5, "total": 15},
        "calculatedTotalCost": 0.01,
    }


def run_poller(monkeypatch, observations: list, overlap, ticks: int = 3, seed_at: int = T0 - 100):
    """Run periodic_ledger_poller for `ticks` polls, 300 s apart, starting at T0 + 360.

    `observations` is a list of (id, start, visible_at) epoch tuples. Returns the ledger rows and the
    `since` value of every poll.
    """
    clock = [T0 + 360]
    since_values: list = []
    sleeps: list = []
    ledger = FakeLedger({"seed": seed_at})

    def fetch(since):
        since_values.append(since)
        lower = since.timestamp()
        return iter([_row(i, s) for i, s, visible in observations if lower <= s and visible <= clock[0]])

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        if len(sleeps) >= ticks:
            raise StopLoop

    monkeypatch.setenv("LEDGER_POLL_INTERVAL", "300")
    if overlap is None:
        monkeypatch.delenv("LEDGER_OVERLAP_MINUTES", raising=False)
    else:
        monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", overlap)
    alerts = MagicMock(**{"get_alerted_keys.return_value": set(), "try_claim_alert.return_value": False})
    with patch("open_webui.env.BILLING_ENABLED", True), \
         patch("open_webui.models.usage_ledger.UsageLedgerDB", ledger), \
         patch("open_webui.models.billing_alert_state.BillingAlertStateDB", alerts), \
         patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=2.0), \
         patch("open_webui.models.users.Users.get_super_admin_user", return_value=None), \
         patch("open_webui.langfuse.observations.fetch_observations_since", side_effect=fetch), \
         patch("open_webui.tasks.billing.asyncio.sleep", fake_sleep):
        with pytest.raises(StopLoop):
            asyncio.run(billing.periodic_ledger_poller())
    return ledger.rows, since_values


# X starts at T0 and becomes visible 10 minutes later. Y starts 5 minutes after T0 and is visible at once,
# so the first poll (at T0 + 6 min) stores Y and moves the newest stored time past X's start.
LATE = ("X", T0, T0 + 10 * MINUTE)
EARLY = ("Y", T0 + 5 * MINUTE, T0 + 5 * MINUTE)


@pytest.fixture(autouse=True)
def reset_warned_values():
    billing._warned_overlap_values.clear()
    yield
    billing._warned_overlap_values.clear()


class TestLateObservationIsCaught:
    def test_default_overlap_catches_an_observation_visible_10_minutes_after_its_start(self, monkeypatch):
        rows, _ = run_poller(monkeypatch, [LATE, EARLY], overlap=None)
        assert set(rows) == {"seed", "X", "Y"}

    def test_30_minute_overlap_catches_it(self, monkeypatch):
        rows, _ = run_poller(monkeypatch, [LATE, EARLY], overlap="30")
        assert "X" in rows

    def test_2_minute_overlap_misses_it(self, monkeypatch):
        """With the previous fixed overlap the late observation is never stored."""
        rows, _ = run_poller(monkeypatch, [LATE, EARLY], overlap="2")
        assert "X" not in rows and "Y" in rows

    def test_an_observation_older_than_the_overlap_is_missed(self, monkeypatch):
        """The overlap is a bound: X starts 5 minutes before the newest stored one, so 4 minutes misses it."""
        rows, _ = run_poller(monkeypatch, [LATE, EARLY], overlap="4")
        assert "X" not in rows

    def test_an_observation_exactly_at_the_overlap_start_is_caught(self, monkeypatch):
        """The read window starts at since inclusive, so 5 minutes just reaches X."""
        rows, _ = run_poller(monkeypatch, [LATE, EARLY], overlap="5")
        assert "X" in rows

    def test_poll_reads_from_the_newest_stored_time_minus_the_overlap(self, monkeypatch):
        _, since_values = run_poller(monkeypatch, [LATE, EARLY], overlap="30")
        assert since_values[0].timestamp() == T0 - 100 - 30 * MINUTE
        assert since_values[1].timestamp() == T0 + 5 * MINUTE - 30 * MINUTE

    def test_a_longer_overlap_moves_the_start_back_by_that_much(self, monkeypatch):
        _, since_values = run_poller(monkeypatch, [LATE, EARLY], overlap="45")
        assert since_values[1].timestamp() == T0 + 5 * MINUTE - 45 * MINUTE

    def test_re_reading_stored_observations_does_not_duplicate_them(self, monkeypatch):
        rows, _ = run_poller(monkeypatch, [LATE, EARLY], overlap="30", ticks=4)
        assert sorted(rows) == ["X", "Y", "seed"]


@pytest.mark.parametrize("value", ["abc", "0", "-5", "2.5", "", "   ", "10081", "1e3"])
def test_invalid_overlap_does_not_stop_the_poller_and_uses_the_default(monkeypatch, value):
    rows, since_values = run_poller(monkeypatch, [LATE, EARLY], overlap=value)
    assert "X" in rows
    assert since_values[1].timestamp() == T0 + 5 * MINUTE - 30 * MINUTE


class TestOverlapMinutes:
    def test_unset_gives_the_default(self, monkeypatch):
        monkeypatch.delenv("LEDGER_OVERLAP_MINUTES", raising=False)
        assert billing._overlap_minutes() == 30

    @pytest.mark.parametrize(
        "raw,expected",
        [("1", 1), ("2", 2), ("30", 30), ("45", 45), (" 10 ", 10), ("10080", 10080), ("007", 7)],
    )
    def test_valid_values_are_used(self, monkeypatch, raw, expected, caplog):
        monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", raw)
        with caplog.at_level("WARNING", logger="open_webui.tasks.billing"):
            assert billing._overlap_minutes() == expected
        assert "LEDGER_OVERLAP_MINUTES" not in caplog.text

    @pytest.mark.parametrize("raw", ["abc", "0", "-1", "-30", "2.5", "10081", "99999999999999999999", "1e3", "30m"])
    def test_invalid_values_give_the_default_and_a_warning(self, monkeypatch, raw, caplog):
        monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", raw)
        with caplog.at_level("WARNING", logger="open_webui.tasks.billing"):
            assert billing._overlap_minutes() == 30
        assert caplog.text.count("LEDGER_OVERLAP_MINUTES") == 1
        assert f"LEDGER_OVERLAP_MINUTES={raw!r} is not an integer from 1 to 10080; using 30." in caplog.text

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_empty_value_is_the_same_as_unset_without_a_warning(self, monkeypatch, raw, caplog):
        monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", raw)
        with caplog.at_level("WARNING", logger="open_webui.tasks.billing"):
            assert billing._overlap_minutes() == 30
        assert caplog.text == ""

    def test_the_same_invalid_value_is_reported_once(self, monkeypatch, caplog):
        monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", "abc")
        with caplog.at_level("WARNING", logger="open_webui.tasks.billing"):
            for _ in range(3):
                billing._overlap_minutes()
        assert caplog.text.count("LEDGER_OVERLAP_MINUTES") == 1

    def test_a_different_invalid_value_is_reported_again(self, monkeypatch, caplog):
        with caplog.at_level("WARNING", logger="open_webui.tasks.billing"):
            monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", "abc")
            billing._overlap_minutes()
            monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", "-1")
            billing._overlap_minutes()
        assert caplog.text.count("LEDGER_OVERLAP_MINUTES") == 2

    def test_the_value_is_read_on_every_call(self, monkeypatch):
        monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", "10")
        assert billing._overlap_minutes() == 10
        monkeypatch.setenv("LEDGER_OVERLAP_MINUTES", "20")
        assert billing._overlap_minutes() == 20

    def test_the_default_is_30_minutes_and_the_cap_is_7_days(self):
        assert billing._DEFAULT_OVERLAP_MINUTES == 30
        assert billing._MAX_OVERLAP_MINUTES == 7 * 24 * 60
