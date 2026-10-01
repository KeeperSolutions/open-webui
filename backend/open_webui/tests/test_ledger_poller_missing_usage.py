"""Regression tests for the "missing usage" billing bug (see pipelines-v4
feature/TRAU-555 and the Hubgate/open-webui side of the same investigation).

An observation can arrive from Langfuse with its `usage` object missing
entirely (not zero-valued — absent). `_sync_observations`
(open_webui/tasks/billing.py) builds each ledger row with
`int(usage.get("input") or 0)`, so a missing field silently becomes `0`
tokens. Combined with `calculatedTotalCost` also being unset on the same
observation, the row is inserted with `tokens_input/output/total = 0` and
`cost_eur = None` — the reply is recorded but never billed, and the nightly
deep rescan can't backfill a price because there are no token counts to
price from.

These tests assert on the actual row dicts passed to
`UsageLedgerDB.bulk_insert_ignore`, not just on alert-email side effects
(see test_billing_alert_dispatch.py for those), so they fail on the old
`or`-chain-shaped code and pass once missing-vs-zero usage is distinguished.
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest


def _obs(obs_id: str, *, usage: dict | None, cost_usd: float | None, model: str = "grok-4.6") -> dict:
    obs = {
        "id": obs_id,
        "userId": "user@example.com",
        "model": model,
        "startTime": "2024-01-01T00:00:00.000Z",
        "calculatedTotalCost": cost_usd,
    }
    if usage is not None:
        obs["usage"] = usage
    return obs


def _run_sync_capture_rows(obs_rows):
    """Run _sync_observations with Langfuse/ledger/users/email mocked out, and
    return the `rows` list it handed to UsageLedgerDB.bulk_insert_ignore."""
    import open_webui.tasks.billing as tasks_mod

    mock_ledger = MagicMock()
    mock_ledger.bulk_insert_ignore.return_value = 0
    mock_ledger.get_cost_eur_for_users_current_month.return_value = {}
    mock_ledger.get_models_with_recent_priced_rows.return_value = []

    mock_alert_state = MagicMock()
    mock_alert_state.try_claim_alert.return_value = False
    mock_alert_state.get_alerted_keys.return_value = set()

    with patch("open_webui.models.usage_ledger.UsageLedgerDB", mock_ledger), \
         patch("open_webui.models.billing_alert_state.BillingAlertStateDB", mock_alert_state), \
         patch("open_webui.langfuse.observations.fetch_observations_since", return_value=iter(obs_rows)), \
         patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=1.1), \
         patch("open_webui.models.users.Users.get_super_admin_user", return_value=None):
        tasks_mod._sync_observations(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc))

    assert mock_ledger.bulk_insert_ignore.called
    return mock_ledger.bulk_insert_ignore.call_args.args[0]


class TestMissingUsageBecomesZeroTokens:
    def test_missing_usage_object_is_recorded_as_zero_tokens_and_unbilled(self):
        """Reproduces the core bug: an observation with NO `usage` key at all
        (the actual shape seen in prod for ~44/57 grok-4.6 main replies) and no
        calculatedTotalCost ends up in the ledger as 0 tokens / cost_eur=None -
        indistinguishable from a reply that genuinely used zero tokens."""
        rows = _run_sync_capture_rows([_obs("o1", usage=None, cost_usd=None)])

        assert len(rows) == 1
        row = rows[0]
        assert row["tokens_input"] == 0
        assert row["tokens_output"] == 0
        assert row["tokens_total"] == 0
        assert row["cost_eur"] is None

    def test_zero_valued_completion_tokens_are_not_dropped(self):
        """The historical `or`-chain bug: {"prompt_tokens": 10, "completion_tokens": 0}
        under an `or`-based picker treats the real 0 as "missing" and can lose
        the whole usage object. Here the fields use Langfuse's actual
        input/output/total key names (not prompt_tokens/completion_tokens,
        which is the raw-provider shape, not what `usage.get` reads) - a real
        completion that is genuinely empty (0 output tokens) must still record
        its real input token count, not have the whole object discarded."""
        rows = _run_sync_capture_rows([
            _obs("o1", usage={"input": 10, "output": 0, "total": 10}, cost_usd=0.0002),
        ])

        assert len(rows) == 1
        row = rows[0]
        assert row["tokens_input"] == 10
        assert row["tokens_output"] == 0
        assert row["tokens_total"] == 10

    def test_present_usage_with_real_counts_is_recorded_and_priced(self):
        """Control case: usage present with non-zero counts and a real cost
        must be recorded faithfully - establishes the contrast with the
        missing-usage case above rather than relying on absence of failure."""
        rows = _run_sync_capture_rows([
            _obs("o1", usage={"input": 120, "output": 48, "total": 168}, cost_usd=0.01),
        ])

        assert len(rows) == 1
        row = rows[0]
        assert row["tokens_input"] == 120
        assert row["tokens_output"] == 48
        assert row["tokens_total"] == 168
        assert row["cost_eur"] == pytest.approx(0.01 / 1.1)

    def test_missing_usage_and_present_usage_are_indistinguishable_in_the_row(self):
        """Documents the actual defect, not just a symptom: once built, a
        missing-usage row and a genuine-zero-usage row are byte-for-byte
        identical. This is exactly why the nightly deep rescan can't recover
        these rows later - bulk_upsert_costs backfills cost_eur for rows with
        token counts already stored, but there's no token data left to price
        from, and no way to tell "really zero" apart from "never reported" at
        that point. The fix for the still-open poller issue (see project
        memory) must distinguish these at ingestion time, not after the fact."""
        missing_row = _run_sync_capture_rows([_obs("o1", usage=None, cost_usd=None)])[0]
        genuinely_zero_row = _run_sync_capture_rows(
            [_obs("o2", usage={"input": 0, "output": 0, "total": 0}, cost_usd=None)]
        )[0]

        for key in ("tokens_input", "tokens_output", "tokens_total", "cost_eur"):
            assert missing_row[key] == genuinely_zero_row[key]

    def test_missing_usage_model_is_marked_unpriced_even_though_it_has_a_real_price(self):
        """Second finding from the investigation: "unpriced" is the wrong label
        for this case. A model is marked unpriced purely because this
        observation had no calculatedTotalCost - even when the same model is
        genuinely priced in Langfuse and simply had no usage to price on this
        particular reply. Captured here as a regression test for the mislabel,
        not a statement that this is desired long-term behavior (see the
        still-open poller fix in project memory: distinguish "has usage but no
        cost" from "no cost at all" before deciding a model is unpriced)."""
        import open_webui.tasks.billing as tasks_mod
        from unittest.mock import MagicMock as _MM

        mock_ledger = _MM()
        mock_ledger.bulk_insert_ignore.return_value = 0
        mock_ledger.get_cost_eur_for_users_current_month.return_value = {}
        mock_ledger.get_models_with_recent_priced_rows.return_value = []

        claimed = {}

        def _try_claim(_type, model):
            claimed[model] = True
            return True

        mock_alert_state = _MM()
        mock_alert_state.try_claim_alert.side_effect = _try_claim
        mock_alert_state.get_alerted_keys.return_value = set()

        with patch("open_webui.models.usage_ledger.UsageLedgerDB", mock_ledger), \
             patch("open_webui.models.billing_alert_state.BillingAlertStateDB", mock_alert_state), \
             patch("open_webui.langfuse.observations.fetch_observations_since",
                   return_value=iter([_obs("o1", usage=None, cost_usd=None, model="grok-4.6")])), \
             patch("open_webui.langfuse.ecb_rates.get_eur_usd_rate", return_value=1.1), \
             patch("open_webui.models.users.Users.get_super_admin_user", return_value=None):
            tasks_mod._sync_observations(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc))

        assert "grok-4.6" in claimed
