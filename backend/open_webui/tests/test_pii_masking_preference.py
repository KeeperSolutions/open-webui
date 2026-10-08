"""A user's stored PII masking preference and the instance default under it."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules.setdefault("stripe", MagicMock())

from open_webui.utils.pii_masking_preference import (
    effective_pii_masking,
    instance_pii_masking_default,
    preference_of,
    stored_pii_masking,
    ui_keeping_stored_pii,
    ui_with_pii_preference,
)


def _settings(value=None, fid="pii_filter", extra=None):
    entry = dict(extra or {})
    if value is not None:
        entry["pii_masking_enabled"] = value
    return {"ui": {"theme": "dark", "pipelines": {"valves": {fid: entry}}}}


def test_stored_value_is_read_from_any_pii_filter_id():
    assert stored_pii_masking(_settings(False, "pii_filter_pipeline")) is False


def test_missing_or_non_boolean_value_is_unset():
    assert stored_pii_masking({}) is None
    assert stored_pii_masking(None) is None
    assert stored_pii_masking(_settings("no")) is None
    assert stored_pii_masking({"ui": "garbage"}) is None


def test_preference_of_maps_three_states():
    assert preference_of({}) == "default"
    assert preference_of(_settings(True)) == "on"
    assert preference_of(_settings(False)) == "off"


def test_effective_value_falls_back_to_instance_default_only_when_unset():
    assert effective_pii_masking({}, False) is False
    assert effective_pii_masking({}, True) is True
    assert effective_pii_masking(_settings(True), False) is True
    assert effective_pii_masking(_settings(False), True) is False


def test_instance_default_is_on_unless_explicitly_false():
    req = MagicMock()
    req.app.state.config.PII_MASKING_DEFAULT_ENABLED = False
    assert instance_pii_masking_default(req) is False
    req.app.state.config.PII_MASKING_DEFAULT_ENABLED = MagicMock()
    assert instance_pii_masking_default(req) is True
    assert instance_pii_masking_default(SimpleNamespace()) is True


def test_writing_a_preference_touches_only_the_masking_key():
    ui = _settings(True, extra={"other_valve": 3})["ui"]
    out = ui_with_pii_preference(ui, "off")
    assert out["theme"] == "dark"
    assert out["pipelines"]["valves"]["pii_filter"] == {"other_valve": 3, "pii_masking_enabled": False}
    assert out["pipelines"]["valves"]["pii_filter_pipeline"] == {"pii_masking_enabled": False}
    # Input is not mutated.
    assert ui["pipelines"]["valves"]["pii_filter"]["pii_masking_enabled"] is True


def test_default_preference_removes_the_key_and_empty_entries():
    out = ui_with_pii_preference(_settings(False)["ui"], "default")
    assert "pii_filter" not in out["pipelines"]["valves"]
    assert stored_pii_masking({"ui": out}) is None


def test_generic_save_keeps_the_stored_preference():
    stale = _settings(True)["ui"]  # tab still believes ON
    stored = _settings(False)  # admin has since set OFF
    assert stored_pii_masking({"ui": ui_keeping_stored_pii(stale, stored)}) is False
    assert stored_pii_masking({"ui": ui_keeping_stored_pii(stale, {})}) is None


def test_config_var_defaults_on():
    from open_webui.config import PII_MASKING_DEFAULT_ENABLED

    assert PII_MASKING_DEFAULT_ENABLED.value is True
