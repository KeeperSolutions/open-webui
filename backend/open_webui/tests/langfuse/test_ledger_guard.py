"""Tests for langfuse/ledger_guard.py: refusing to insert observations whose ids do not match the ledger.

Ledger ids and fetched ids are synthetic. The guard is exercised directly, through
fetch_observations_since, through the ledger sync and through the poller loop.
"""

import asyncio
import datetime
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import open_webui.langfuse.ledger_guard as guard
import open_webui.langfuse.observations as obs_module
import open_webui.tasks.billing as billing
from open_webui.models.usage_ledger import UsageLedger
from open_webui.tests.langfuse.test_ledger_overlap import FakeLedger, StopLoop
from open_webui.tests.langfuse.test_observations import GET, NOW, SINCE, FakeLangfuse
from open_webui.tests.langfuse.test_v2_observations_shape import make_v2_page, make_v2_row

LEDGER_IDS = "open_webui.langfuse.ledger_guard.ledger_ids_between"
WINDOW_START = datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
WINDOW_END = WINDOW_START + datetime.timedelta(minutes=60)


def ids(n: int, prefix: str = "led") -> list:
    return [f"{prefix}{i:05d}" for i in range(n)]


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    for name in ("LEDGER_ID_GUARD_MIN_ROWS", "LEDGER_ID_GUARD_MIN_RATIO", "LEDGER_ID_GUARD_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    guard._trips_total = 0
    guard._warned_values.clear()
    obs_module._bad_cost_total = 0
    obs_module._bad_cost_seen.clear()
    obs_module._missing_user_id_seen.clear()
    yield
    guard._trips_total = 0
    guard._warned_values.clear()


@pytest.fixture(autouse=True)
def patch_fetch_environment():
    with patch("open_webui.langfuse.observations.load_env", return_value=("pk", "sk", "https://host")), \
         patch("open_webui.langfuse.observations._now_utc", return_value=NOW):
        yield


def check(ledger: list, fetched, since=WINDOW_START, until=WINDOW_END):
    with patch(LEDGER_IDS, return_value=set(ledger)):
        guard.check_ledger_overlap(fetched, since, until)


class TestDecision:
    def test_same_ids_pass(self, caplog):
        """The ledger rows and the fetched ids are the same, so nothing is raised or logged."""
        with caplog.at_level("ERROR", logger="open_webui.langfuse.ledger_guard"):
            check(ids(30), ids(30))
        assert guard._trips_total == 0 and caplog.text == ""

    def test_shifted_ids_raise_with_counts_and_no_ids(self, caplog):
        """All fetched ids differ from the ledger ids: raise, log one error with counts, count one trip."""
        ledger, fetched = ids(30), ids(30, prefix="new")
        with caplog.at_level("ERROR", logger="open_webui.langfuse.ledger_guard"):
            with pytest.raises(guard.LedgerIdMismatch) as raised:
                check(ledger, fetched)
        assert guard._trips_total == 1
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        message = (
            "30 of 30 ledger rows in the window 2026-01-01T10:00:00+00:00 to 2026-01-01T10:45:00+00:00 "
            "were not returned by Langfuse (30 observations fetched, 0 matched, 0% of ledger rows; "
            "minimum 80% from 20 rows)"
        )
        expected_log = f"[ledger-poller] id guard: {message}; nothing is inserted (occurrences since start: 1)"
        assert errors[0].getMessage() == expected_log
        assert str(raised.value) == f"Ledger id guard: {message}"
        assert "led0" not in errors[0].getMessage() and "new0" not in errors[0].getMessage()

    def test_a_partial_match_reports_the_missing_and_matched_counts(self, caplog):
        """79 of 100 ledger rows are found among 89 fetched ids: 21 are missing, 79% matched."""
        with caplog.at_level("ERROR", logger="open_webui.langfuse.ledger_guard"):
            with pytest.raises(guard.LedgerIdMismatch) as raised:
                check(ids(100), ids(79) + ids(10, prefix="other"))
        assert str(raised.value) == (
            "Ledger id guard: 21 of 100 ledger rows in the window 2026-01-01T10:00:00+00:00 to "
            "2026-01-01T10:45:00+00:00 were not returned by Langfuse (89 observations fetched, 79 matched, "
            "79% of ledger rows; minimum 80% from 20 rows)"
        )

    def test_the_message_shows_the_configured_thresholds(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", "5")
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", "0.5")
        with pytest.raises(guard.LedgerIdMismatch, match=r"minimum 50% from 5 rows\)$"):
            check(ids(10), ids(4))

    def test_the_error_is_a_runtime_error(self):
        assert issubclass(guard.LedgerIdMismatch, RuntimeError)

    def test_each_trip_is_counted(self):
        for expected in (1, 2, 3):
            with pytest.raises(guard.LedgerIdMismatch):
                check(ids(25), [])
            assert guard._trips_total == expected

    def test_fetched_ids_that_include_all_ledger_ids_pass_even_when_there_are_many_more(self):
        """The share is taken of the ledger rows, not of the fetched ids."""
        check(ids(20), ids(20) + ids(1000, prefix="extra"))

    def test_a_small_overlap_with_a_large_ledger_raises(self):
        """10 of 100 ledger rows are found; the share of the fetched ids (10 of 10) is irrelevant."""
        with pytest.raises(guard.LedgerIdMismatch):
            check(ids(100), ids(10))

    def test_an_empty_fetch_with_enough_ledger_rows_raises(self):
        with pytest.raises(guard.LedgerIdMismatch):
            check(ids(25), [])

    def test_fetched_ids_can_be_any_iterable_with_duplicates(self):
        check(ids(25), (i for i in ids(25) + ids(25)))


class TestThresholds:
    @pytest.mark.parametrize("rows,trips", [(19, False), (20, True), (21, True)])
    def test_minimum_rows_boundary(self, rows, trips):
        """Fewer than 20 ledger rows in the window: the check does not apply, even with no match."""
        if trips:
            with pytest.raises(guard.LedgerIdMismatch):
                check(ids(rows), [])
        else:
            check(ids(rows), [])

    @pytest.mark.parametrize("matched,trips", [(81, False), (80, False), (79, True), (0, True), (100, False)])
    def test_default_ratio_boundary_over_100_rows(self, matched, trips):
        """A share of exactly 80% passes; one row fewer raises."""
        ledger = ids(100)
        fetched = ids(matched) + ids(50, prefix="other")
        if trips:
            with pytest.raises(guard.LedgerIdMismatch):
                check(ledger, fetched)
        else:
            check(ledger, fetched)

    @pytest.mark.parametrize("matched,trips", [(50, False), (49, True)])
    def test_ratio_from_the_environment(self, monkeypatch, matched, trips):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", "0.5")
        if trips:
            with pytest.raises(guard.LedgerIdMismatch):
                check(ids(100), ids(matched))
        else:
            check(ids(100), ids(matched))

    @pytest.mark.parametrize("rows,trips", [(4, False), (5, True)])
    def test_minimum_rows_from_the_environment(self, monkeypatch, rows, trips):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", "5")
        if trips:
            with pytest.raises(guard.LedgerIdMismatch):
                check(ids(rows), [])
        else:
            check(ids(rows), [])

    def test_a_very_large_minimum_effectively_disables_the_check(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", "1000000000")
        check(ids(500), [])

    def test_ratio_one_requires_every_ledger_row(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", "1")
        check(ids(40), ids(40))
        with pytest.raises(guard.LedgerIdMismatch):
            check(ids(40), ids(39))

    def test_defaults(self):
        assert (guard._DEFAULT_MIN_ROWS, guard._DEFAULT_MIN_RATIO) == (20, 0.8)
        assert guard._SETTLE == datetime.timedelta(minutes=15)


class TestEnvironmentParsing:
    @pytest.mark.parametrize("raw,expected", [("1", 1), ("20", 20), (" 7 ", 7), ("500", 500)])
    def test_valid_minimum_rows(self, monkeypatch, raw, expected, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", raw)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            assert guard._min_rows() == expected
        assert caplog.text == ""

    @pytest.mark.parametrize("raw", ["0", "-3", "abc", "2.5", "1e2"])
    def test_invalid_minimum_rows_gives_the_default_with_one_warning(self, monkeypatch, raw, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", raw)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            assert guard._min_rows() == 20
            assert guard._min_rows() == 20
        assert caplog.text.count("LEDGER_ID_GUARD_MIN_ROWS") == 1
        assert f"[ledger-poller] LEDGER_ID_GUARD_MIN_ROWS={raw!r} is not valid; using 20." in caplog.text

    @pytest.mark.parametrize("raw,expected", [("0.5", 0.5), ("1", 1.0), ("1.0", 1.0), ("0.01", 0.01), (" 0.9 ", 0.9)])
    def test_valid_ratio(self, monkeypatch, raw, expected, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", raw)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            assert guard._min_ratio() == expected
        assert caplog.text == ""

    @pytest.mark.parametrize("raw", ["0", "0.0", "-0.5", "1.01", "2", "nan", "inf", "abc", "80%"])
    def test_invalid_ratio_gives_the_default_with_one_warning(self, monkeypatch, raw, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", raw)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            assert guard._min_ratio() == 0.8
            assert guard._min_ratio() == 0.8
        assert caplog.text.count("LEDGER_ID_GUARD_MIN_RATIO") == 1
        assert f"[ledger-poller] LEDGER_ID_GUARD_MIN_RATIO={raw!r} is not valid; using 0.8." in caplog.text

    def test_the_same_invalid_value_for_the_two_settings_is_reported_for_each(self, monkeypatch, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", "abc")
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", "abc")
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            guard._min_rows()
            guard._min_ratio()
        assert "LEDGER_ID_GUARD_MIN_ROWS='abc'" in caplog.text and "LEDGER_ID_GUARD_MIN_RATIO='abc'" in caplog.text

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_empty_values_are_the_same_as_unset(self, monkeypatch, raw, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", raw)
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", raw)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            assert (guard._min_rows(), guard._min_ratio()) == (20, 0.8)
        assert caplog.text == ""

    def test_invalid_values_do_not_stop_the_check(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", "abc")
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", "abc")
        with pytest.raises(guard.LedgerIdMismatch):
            check(ids(25), [])


OFF_WARNING = "[ledger-poller] LEDGER_ID_GUARD_ENABLED={raw}: the id guard is off; duplicate rows are not prevented."


class TestEnabledSwitch:
    @pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE", "Off", " false ", "NO"])
    def test_switched_off_does_not_read_the_ledger_and_does_not_raise(self, monkeypatch, raw):
        """With the guard off, a total mismatch passes and the ledger is not read."""
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", raw)
        with patch(LEDGER_IDS, return_value=set(ids(100))) as read:
            guard.check_ledger_overlap([], WINDOW_START, WINDOW_END)
        read.assert_not_called()
        assert guard._trips_total == 0

    @pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "TRUE", " On "])
    def test_explicitly_on_keeps_the_check(self, monkeypatch, raw, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", raw)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            with pytest.raises(guard.LedgerIdMismatch):
                check(ids(30), [])
        assert "LEDGER_ID_GUARD_ENABLED" not in caplog.text

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_empty_value_is_the_same_as_unset(self, monkeypatch, raw, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", raw)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            with pytest.raises(guard.LedgerIdMismatch):
                check(ids(30), [])
        assert "LEDGER_ID_GUARD_ENABLED" not in caplog.text

    def test_unset_keeps_the_check(self):
        with pytest.raises(guard.LedgerIdMismatch):
            check(ids(30), [])

    @pytest.mark.parametrize("raw", ["maybe", "2", "disabled", "falsee", "nope", "f", "n"])
    def test_an_unrecognised_value_keeps_the_check_on_and_warns_once(self, monkeypatch, raw, caplog):
        """A typo must not switch the guard off."""
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", raw)
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            for _ in range(2):
                with pytest.raises(guard.LedgerIdMismatch):
                    check(ids(30), [])
        assert caplog.text.count(f"[ledger-poller] LEDGER_ID_GUARD_ENABLED={raw!r} is not valid; using true.") == 1

    def test_switching_off_warns_once_per_process_with_the_value(self, monkeypatch, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "false")
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            for _ in range(3):
                guard.check_ledger_overlap([], WINDOW_START, WINDOW_END)
        assert caplog.text.count("the id guard is off") == 1
        assert OFF_WARNING.format(raw="false") in caplog.text

    def test_a_failing_ledger_read_does_not_stop_the_sync_when_switched_off(self, monkeypatch):
        """The case the switch exists for: the guard's own ledger read fails."""
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "false")
        error = OperationalError("select", {}, Exception("no such table"))
        with patch(LEDGER_IDS, side_effect=error):
            guard.check_ledger_overlap(ids(30), WINDOW_START, WINDOW_END)

    def test_a_failing_ledger_read_still_raises_when_on(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "true")
        error = OperationalError("select", {}, Exception("no such table"))
        with patch(LEDGER_IDS, side_effect=error):
            with pytest.raises(OperationalError):
                guard.check_ledger_overlap(ids(30), WINDOW_START, WINDOW_END)

    def test_the_switch_is_read_on_every_call(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "false")
        check(ids(30), [])
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "true")
        with pytest.raises(guard.LedgerIdMismatch):
            check(ids(30), [])

    def test_the_other_settings_are_not_consulted_when_off(self, monkeypatch, caplog):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "off")
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_ROWS", "abc")
        monkeypatch.setenv("LEDGER_ID_GUARD_MIN_RATIO", "abc")
        with caplog.at_level("WARNING", logger="open_webui.langfuse.ledger_guard"):
            guard.check_ledger_overlap([], WINDOW_START, WINDOW_END)
        assert "MIN_ROWS" not in caplog.text and "MIN_RATIO" not in caplog.text

    def test_the_name_and_accepted_values_are_fixed(self):
        assert guard._FALSE_VALUES == ("false", "0", "no", "off")
        assert guard._TRUE_VALUES == ("true", "1", "yes", "on")


class TestWindow:
    def test_the_ledger_is_read_for_since_to_until_minus_15_minutes(self):
        with patch(LEDGER_IDS, return_value=set()) as read:
            guard.check_ledger_overlap([], WINDOW_START, WINDOW_END)
        read.assert_called_once_with(
            int(WINDOW_START.timestamp()), int((WINDOW_END - datetime.timedelta(minutes=15)).timestamp())
        )

    def test_a_window_no_longer_than_15_minutes_skips_the_check(self):
        with patch(LEDGER_IDS) as read:
            guard.check_ledger_overlap([], WINDOW_START, WINDOW_START + datetime.timedelta(minutes=15))
            guard.check_ledger_overlap([], WINDOW_START, WINDOW_START + datetime.timedelta(minutes=5))
        read.assert_not_called()

    def test_a_window_just_over_15_minutes_is_read(self):
        with patch(LEDGER_IDS, return_value=set()) as read:
            guard.check_ledger_overlap([], WINDOW_START, WINDOW_START + datetime.timedelta(minutes=15, seconds=1))
        read.assert_called_once()

    def test_a_failed_ledger_read_raises_and_is_not_a_mismatch(self):
        error = OperationalError("select", {}, Exception("no such table"))
        with patch(LEDGER_IDS, side_effect=error):
            with pytest.raises(OperationalError):
                guard.check_ledger_overlap(ids(30), WINDOW_START, WINDOW_END)
        assert guard._trips_total == 0


class TestLedgerIdsBetween:
    """The real query, on an in-memory SQLite table."""

    @pytest.fixture
    def session(self):
        engine = create_engine("sqlite:///:memory:")
        UsageLedger.__table__.create(engine, checkfirst=True)
        db = sessionmaker(bind=engine)()
        yield db
        db.close()
        UsageLedger.__table__.drop(engine)

    @staticmethod
    def add(db, obs_id: str, observed_at: int):
        db.add(
            UsageLedger(
                id=f"row-{obs_id}",
                langfuse_observation_id=obs_id,
                user_id="u",
                model="m",
                observed_at=observed_at,
                synced_at=observed_at,
            )
        )
        db.commit()

    def read(self, db, start: int, end: int) -> set:
        @contextmanager
        def get_db():
            yield db

        with patch("open_webui.langfuse.ledger_guard.get_db", get_db):
            return guard.ledger_ids_between(start, end)

    def test_start_is_inclusive_and_end_is_exclusive(self, session):
        stored = {"before": 99, "at_start": 100, "inside": 150, "last": 199, "at_end": 200, "after": 300}
        for name, at in stored.items():
            self.add(session, name, at)
        assert self.read(session, 100, 200) == {"at_start", "inside", "last"}

    def test_an_empty_table_gives_an_empty_set(self, session):
        assert self.read(session, 0, 10**12) == set()

    def test_returns_observation_ids_not_row_ids(self, session):
        self.add(session, "obs-1", 10)
        assert self.read(session, 0, 100) == {"obs-1"}

    def test_end_equal_to_start_gives_nothing(self, session):
        self.add(session, "x", 100)
        assert self.read(session, 100, 100) == set()


def v2_page(prefix: str, n: int, cursor=None) -> dict:
    rows = [make_v2_row(i + 1, id=f"{prefix}{i:05d}", userId="user@example.test") for i in range(n)]
    return make_v2_page(rows, cursor=cursor)


class TestThroughFetch:
    def fetch(self, pages, ledger: list):
        fake = FakeLangfuse(pages)
        with patch(LEDGER_IDS, return_value=set(ledger)) as read, patch(GET, side_effect=fake.get):
            return list(obs_module.fetch_observations_since(SINCE)), read

    def test_matching_ids_return_every_row(self):
        results, _ = self.fetch([v2_page("led", 25)], ids(25))
        assert [r["id"] for r in results] == ids(25)

    def test_ids_from_every_page_are_compared(self):
        pages = [v2_page("led", 15, cursor="c1"), make_v2_page([make_v2_row(99, id=i) for i in ids(25)[15:]])]
        results, _ = self.fetch(pages, ids(25))
        assert len(results) == 25

    def test_shifted_ids_raise_before_any_row_is_returned(self):
        fake = FakeLangfuse([v2_page("new", 25)])
        with patch(LEDGER_IDS, return_value=set(ids(25))), patch(GET, side_effect=fake.get):
            generator = obs_module.fetch_observations_since(SINCE)
            with pytest.raises(guard.LedgerIdMismatch):
                next(generator)

    def test_too_few_ledger_rows_do_not_block(self):
        results, _ = self.fetch([v2_page("new", 5)], ids(5))
        assert len(results) == 5

    @pytest.mark.parametrize("matched,blocked", [(80, False), (79, True)])
    def test_partial_overlap_around_the_threshold(self, matched, blocked):
        page = make_v2_page([make_v2_row(i + 1, id=i_d, userId="u@example.test") for i, i_d in enumerate(ids(matched))])
        if blocked:
            with pytest.raises(guard.LedgerIdMismatch):
                self.fetch([page], ids(100))
        else:
            assert len(self.fetch([page], ids(100))[0]) == matched

    def test_the_window_is_the_one_requested_from_langfuse(self):
        _, read = self.fetch([v2_page("led", 1)], [])
        until = NOW + datetime.timedelta(seconds=60)
        read.assert_called_once_with(int(SINCE.timestamp()), int((until - datetime.timedelta(minutes=15)).timestamp()))

    def test_the_guard_runs_before_rows_are_reported_or_mapped(self):
        """A mismatch raises before the report of unusable costs, so those rows are not counted."""
        page = make_v2_page([make_v2_row(i + 1, id=f"new{i:05d}", totalCost="n/a") for i in range(25)])
        with pytest.raises(guard.LedgerIdMismatch):
            self.fetch([page], ids(25))
        assert obs_module._bad_cost_total == 0

    def test_the_usage_group_check_comes_first(self):
        rows = [make_v2_row(i + 1, id=f"new{i:05d}") for i in range(25)]
        for row in rows:
            del row["usageDetails"], row["totalCost"]
        with pytest.raises(RuntimeError, match="no usage fields"):
            self.fetch([make_v2_page(rows)], ids(25))

    def test_non_string_ids_are_not_counted_as_fetched_observations(self):
        """Rows without a string id are left out of the fetched ids that the guard reports."""
        odd = [make_v2_row(1, id=None), make_v2_row(2, id=7)]
        rows = odd + [make_v2_row(i + 3, id=f"new{i:05d}") for i in range(25)]
        with pytest.raises(guard.LedgerIdMismatch, match=r"\(25 observations fetched, 0 matched"):
            self.fetch([make_v2_page(rows)], ids(25))

    def test_non_string_ids_are_ignored(self):
        odd = [make_v2_row(1, id=None), make_v2_row(2, id=7)]
        rows = odd + [make_v2_row(i + 3, id=x) for i, x in enumerate(ids(25))]
        results, _ = self.fetch([make_v2_page(rows)], ids(25))
        assert len(results) == 27


def run_sync(pages, ledger_ids: list, deep_rescan: bool = False):
    """Run the real ledger sync over fake pages. Returns the ledger mock and the raised error, if any."""
    ledger = MagicMock()
    ledger.bulk_insert_ignore.side_effect = lambda rows: {r["langfuse_observation_id"] for r in rows}
    ledger.bulk_upsert_costs.return_value = set()
    ledger.get_cost_eur_for_users_current_month.return_value = {}
    ledger.get_models_with_recent_priced_rows.return_value = []
    alerts = MagicMock(**{"get_alerted_keys.return_value": set(), "try_claim_alert.return_value": False})
    fake = FakeLangfuse(pages)
    error = None
    with patch("open_webui.models.usage_ledger.UsageLedgerDB", ledger), \
         patch("open_webui.models.billing_alert_state.BillingAlertStateDB", alerts), \
         patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=2.0), \
         patch("open_webui.models.users.Users.get_super_admin_user", return_value=None), \
         patch(LEDGER_IDS, return_value=set(ledger_ids)), \
         patch(GET, side_effect=fake.get):
        try:
            billing._sync_observations(SINCE, deep_rescan=deep_rescan)
        except guard.LedgerIdMismatch as exc:
            error = exc
    return ledger, error


class TestSwitchedOffThroughFetchAndSync:
    def test_shifted_ids_are_returned_and_inserted_when_the_guard_is_off(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "false")
        ledger, error = run_sync([v2_page("new", 25)], ids(25))
        assert error is None
        assert len(ledger.bulk_insert_ignore.call_args.args[0]) == 25

    def test_the_same_input_is_blocked_when_the_guard_is_on(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "true")
        ledger, error = run_sync([v2_page("new", 25)], ids(25))
        assert isinstance(error, guard.LedgerIdMismatch)
        ledger.bulk_insert_ignore.assert_not_called()

    def test_the_ledger_is_not_read_by_a_fetch_when_the_guard_is_off(self, monkeypatch):
        monkeypatch.setenv("LEDGER_ID_GUARD_ENABLED", "false")
        fake = FakeLangfuse([v2_page("new", 25)])
        with patch(LEDGER_IDS) as read, patch(GET, side_effect=fake.get):
            assert len(list(obs_module.fetch_observations_since(SINCE))) == 25
        read.assert_not_called()


class TestThroughTheLedgerSync:
    def test_matching_ids_are_inserted(self):
        ledger, error = run_sync([v2_page("led", 25)], ids(25))
        assert error is None
        assert len(ledger.bulk_insert_ignore.call_args.args[0]) == 25

    def test_shifted_ids_insert_nothing(self):
        ledger, error = run_sync([v2_page("new", 25)], ids(25))
        assert isinstance(error, guard.LedgerIdMismatch)
        ledger.bulk_insert_ignore.assert_not_called()

    def test_shifted_ids_in_the_nightly_rescan_insert_and_backfill_nothing(self):
        ledger, error = run_sync([v2_page("new", 25)], ids(25), deep_rescan=True)
        assert isinstance(error, guard.LedgerIdMismatch)
        ledger.bulk_insert_ignore.assert_not_called()
        ledger.bulk_upsert_costs.assert_not_called()
        ledger.bulk_upsert_user_ids.assert_not_called()

    def test_matching_ids_in_the_nightly_rescan_are_backfilled(self):
        ledger, error = run_sync([v2_page("led", 25)], ids(25), deep_rescan=True)
        assert error is None
        ledger.bulk_upsert_user_ids.assert_called_once()

    def test_too_few_rows_in_the_window_still_insert(self):
        ledger, error = run_sync([v2_page("new", 5)], ids(5))
        assert error is None and len(ledger.bulk_insert_ignore.call_args.args[0]) == 5


class TestPollerLoop:
    def run(self, monkeypatch, pages, ticks=2):
        """Two polls against a ledger with 25 stored rows; returns the ledger rows and the request parameters."""
        base = int(NOW.timestamp()) - 3600
        ledger = FakeLedger({i: base + n * 10 for n, i in enumerate(ids(25))})
        fake = FakeLangfuse(pages)
        sleeps: list = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= ticks:
                raise StopLoop

        def ledger_ids(start, end):
            return {i for i, at in ledger.rows.items() if start <= at < end}

        monkeypatch.setenv("LEDGER_POLL_INTERVAL", "300")
        alerts = MagicMock(**{"get_alerted_keys.return_value": set(), "try_claim_alert.return_value": False})
        with patch("open_webui.env.BILLING_ENABLED", True), \
             patch("open_webui.models.usage_ledger.UsageLedgerDB", ledger), \
             patch("open_webui.models.billing_alert_state.BillingAlertStateDB", alerts), \
             patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=2.0), \
             patch("open_webui.models.users.Users.get_super_admin_user", return_value=None), \
             patch(LEDGER_IDS, side_effect=ledger_ids), \
             patch(GET, side_effect=fake.get), \
             patch("open_webui.tasks.billing.asyncio.sleep", fake_sleep):
            with pytest.raises(StopLoop):
                asyncio.run(billing.periodic_ledger_poller())
        return ledger.rows, fake

    def test_a_mismatch_inserts_nothing_and_the_next_poll_reads_the_same_window(self, monkeypatch, caplog):
        with caplog.at_level("ERROR"):
            rows, fake = self.run(monkeypatch, [v2_page("new", 30), v2_page("new", 30)])
        assert sorted(rows) == ids(25)
        assert len(fake.observation_calls) == 2
        assert fake.params(0)["fromStartTime"] == fake.params(1)["fromStartTime"]
        assert caplog.text.count("[ledger-poller] id guard:") == 2
        assert caplog.text.count("[ledger-poller] Unexpected error: Ledger id guard:") == 2
        assert guard._trips_total == 2

    def test_matching_ids_are_accepted_and_new_rows_are_inserted(self, monkeypatch):
        known = [
            make_v2_row(n + 1, id=i, userId="u@example.test", startTime="2026-06-19T10:00:00.000Z")
            for n, i in enumerate(ids(25))
        ]
        new = make_v2_row(50, id="brand-new", userId="u@example.test", startTime="2026-06-19T11:00:00.000Z")
        page = make_v2_page(known + [new])
        rows, _ = self.run(monkeypatch, [page, page])
        assert "brand-new" in rows and len(rows) == 26


def test_fetch_uses_the_guard_module_function():
    """fetch_observations_since calls ledger_guard.check_ledger_overlap, not a copy of it."""
    fake = FakeLangfuse([make_v2_page([])])
    with patch("open_webui.langfuse.observations.ledger_guard.check_ledger_overlap") as check_mock, \
         patch(GET, side_effect=fake.get):
        list(obs_module.fetch_observations_since(SINCE))
    check_mock.assert_called_once()
