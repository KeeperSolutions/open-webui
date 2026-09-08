"""
Tests for the Featured Models admin config (TRAU-542).

Covers:
  * ModelsConfigForm._validate_featured_models — the pydantic field_validator on
    FEATURED_MODELS that mirrors the frontend limits (provider_name required,
    3–24 chars; at most 3 tags, each at most 10 chars) so API clients / config
    imports can't bypass FeaturedModelsModal.svelte.
  * MODELS_CONFIG_KEYS gaining 'FEATURED_MODELS': 'ui.featured_models' so
    GET /configs/models round-trips the curated list (regression: it used to
    be dropped from the response).
  * set_models_config (POST /configs/models) preserving the existing featured
    list when a caller's request doesn't include FEATURED_MODELS at all, and
    the dedicated set_featured_models (POST /configs/models/featured) endpoint
    updating only that field. These close a reported bug: several admin panels
    (model order, model defaults) call the shared POST /configs/models without
    FEATURED_MODELS in the body. Pydantic used to fill the missing field with
    [] (its old default), which set_models_config then wrote straight through
    — every save from one of those panels silently emptied the curated list.
"""

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

# Isolated test DB + no migrations BEFORE any import that touches config.
_tmpdir = tempfile.mkdtemp()
_db_file = os.path.join(_tmpdir, 'test_featured_models.db')
os.environ['DATABASE_URL'] = f'sqlite:///{_db_file}'
os.environ['ENABLE_DB_MIGRATIONS'] = 'false'
os.environ.setdefault('WEBUI_URL', 'http://localhost:8080')
os.environ.setdefault('CREDITS_PER_EUR_CENT', '1.82')
os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret-key-for-pytest-only')

# stripe is an optional billing dependency not installed in the test env.
sys.modules.setdefault('stripe', MagicMock())

from open_webui.internal.db import engine  # noqa: E402

# Many imports run get_config() at import time — pre-create the config table.
with engine.begin() as conn:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS "config" (
                id INTEGER NOT NULL PRIMARY KEY,
                data JSON NOT NULL,
                version INTEGER NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT OR REPLACE INTO "config" (id, data, version, created_at)
            VALUES (1, '{"version": 0, "ui": {}}', 0, datetime('now'))
            """
        )
    )

from pydantic import ValidationError  # noqa: E402

from open_webui.routers.configs import (  # noqa: E402
    MODELS_CONFIG_KEYS,
    FeaturedModelsForm,
    ModelsConfigForm,
    set_featured_models,
    set_models_config,
)

# ModelsConfigForm requires the other three model keys too; give them harmless
# defaults so every test only varies FEATURED_MODELS.
_BASE = dict(DEFAULT_MODELS=None, DEFAULT_PINNED_MODELS=None, MODEL_ORDER_LIST=[])


def _fake_request(featured_models):
    """A minimal stand-in for FastAPI's Request, carrying just enough of
    request.app.state.config for AppConfig's attribute-style get/set.
    """
    config = SimpleNamespace(
        DEFAULT_MODELS=None,
        DEFAULT_PINNED_MODELS=None,
        MODEL_ORDER_LIST=[],
        FEATURED_MODELS=featured_models,
        DEFAULT_MODEL_METADATA=None,
        DEFAULT_MODEL_PARAMS=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _run(coro):
    return asyncio.run(coro)


def _entry(**overrides):
    entry = {
        'model_id': 'gpt-x',
        'provider_name': 'OpenAI',
        'featured_name': 'Flagship',
        'tags': ['fast', 'smart'],
        'order': 0,
    }
    entry.update(overrides)
    return entry


def _form(featured):
    return ModelsConfigForm(**_BASE, FEATURED_MODELS=featured)


# ---------- MODELS_CONFIG_KEYS ----------


def test_models_config_keys_includes_featured_models():
    # The fix that made GET /configs/models return the curated list.
    assert MODELS_CONFIG_KEYS.get('FEATURED_MODELS') == 'ui.featured_models'


# ---------- valid entries ----------


def test_valid_entry_accepted():
    form = _form([_entry()])
    assert form.FEATURED_MODELS == [_entry()]


def test_empty_list_accepted():
    assert _form([]).FEATURED_MODELS == []


def test_missing_featured_models_is_none_not_empty_list():
    # None (not []) is the signal set_models_config uses to mean "this request
    # didn't touch the featured list, leave it alone" — see
    # test_set_models_config_preserves_featured_models_when_field_omitted.
    assert ModelsConfigForm(**_BASE).FEATURED_MODELS is None


def test_provider_name_boundary_3_accepted():
    _form([_entry(provider_name='abc')])


def test_provider_name_boundary_24_accepted():
    _form([_entry(provider_name='a' * 24)])


def test_tag_boundary_10_accepted():
    _form([_entry(tags=['x' * 10])])


def test_tag_count_boundary_3_accepted():
    _form([_entry(tags=['a', 'b', 'c'])])


def test_provider_name_trimmed_before_length_check():
    # "  abc  " -> "abc" (3 chars) is valid even though the raw string is 7.
    _form([_entry(provider_name='  abc  ')])


def test_non_dict_entries_are_ignored():
    # The validator skips anything that isn't a dict rather than raising.
    form = _form([_entry(), 'not-a-dict', 42, None])
    assert form.FEATURED_MODELS[1] == 'not-a-dict'


def test_entry_without_tags_key_accepted():
    _form([{'model_id': 'm', 'provider_name': 'OpenAI', 'order': 0}])


# ---------- invalid entries ----------


def test_blank_provider_name_rejected():
    with pytest.raises(ValidationError, match='provider_name is required'):
        _form([_entry(provider_name='')])


def test_whitespace_only_provider_name_rejected():
    with pytest.raises(ValidationError, match='provider_name is required'):
        _form([_entry(provider_name='   ')])


def test_missing_provider_name_rejected():
    with pytest.raises(ValidationError, match='provider_name is required'):
        _form([{'model_id': 'm', 'order': 0}])


def test_provider_name_2_chars_rejected():
    with pytest.raises(ValidationError, match='3.24 characters'):
        _form([_entry(provider_name='ab')])


def test_provider_name_25_chars_rejected():
    with pytest.raises(ValidationError, match='3.24 characters'):
        _form([_entry(provider_name='a' * 25)])


def test_tag_11_chars_rejected():
    with pytest.raises(ValidationError, match='exceeds 10 characters'):
        _form([_entry(tags=['x' * 11])])


def test_fourth_tag_rejected():
    with pytest.raises(ValidationError, match='at most'):
        _form([_entry(tags=['a', 'b', 'c', 'd'])])


def test_tag_count_checked_before_tag_length():
    # A 4th tag that's also over-long still reports the count error, not length.
    with pytest.raises(ValidationError, match='at most'):
        _form([_entry(tags=['a', 'b', 'c', 'x' * 99])])


def test_second_entry_invalid_rejected():
    with pytest.raises(ValidationError, match='provider_name is required'):
        _form([_entry(), _entry(model_id='m2', provider_name='')])


def test_error_message_names_the_offending_model_id():
    with pytest.raises(ValidationError, match='gpt-x'):
        _form([_entry(provider_name='ab')])


# ---------- set_models_config: FEATURED_MODELS omission is preserved ----------
# These are the tests for the actual reported bug: an admin panel that doesn't
# edit the featured list (e.g. model order, model defaults) POSTs to
# set_models_config without FEATURED_MODELS in the body, and that save must
# not touch whatever is already there.


@patch('open_webui.routers.configs.publish_event', new_callable=AsyncMock)
def test_set_models_config_preserves_featured_models_when_field_omitted(_publish):
    request = _fake_request(featured_models=[_entry()])

    # A form built without FEATURED_MODELS at all — exactly what
    # ModelDefaultsPanel.svelte / ModelSettingsModal.svelte / Models.svelte's
    # model-order save used to send before this fix.
    form = ModelsConfigForm(**_BASE)
    assert form.FEATURED_MODELS is None

    result = _run(set_models_config(request, form, user=SimpleNamespace(id='admin-1')))

    assert result['FEATURED_MODELS'] == [_entry()]
    assert request.app.state.config.FEATURED_MODELS == [_entry()]


@patch('open_webui.routers.configs.publish_event', new_callable=AsyncMock)
def test_set_models_config_explicit_empty_list_still_clears_it(_publish):
    request = _fake_request(featured_models=[_entry()])

    form = ModelsConfigForm(**_BASE, FEATURED_MODELS=[])
    result = _run(set_models_config(request, form, user=SimpleNamespace(id='admin-1')))

    assert result['FEATURED_MODELS'] == []
    assert request.app.state.config.FEATURED_MODELS == []


@patch('open_webui.routers.configs.publish_event', new_callable=AsyncMock)
def test_set_models_config_explicit_list_replaces_it(_publish):
    request = _fake_request(featured_models=[_entry(model_id='old')])

    form = ModelsConfigForm(**_BASE, FEATURED_MODELS=[_entry(model_id='new')])
    result = _run(set_models_config(request, form, user=SimpleNamespace(id='admin-1')))

    assert result['FEATURED_MODELS'] == [_entry(model_id='new')]


# ---------- set_featured_models (dedicated endpoint) ----------


@patch('open_webui.routers.configs.publish_event', new_callable=AsyncMock)
def test_set_featured_models_writes_only_that_field(_publish):
    request = _fake_request(featured_models=[])
    request.app.state.config.DEFAULT_MODELS = 'should-not-change'

    form = FeaturedModelsForm(FEATURED_MODELS=[_entry()])
    result = _run(set_featured_models(request, form, user=SimpleNamespace(id='admin-1')))

    assert result['FEATURED_MODELS'] == [_entry()]
    assert request.app.state.config.FEATURED_MODELS == [_entry()]
    assert request.app.state.config.DEFAULT_MODELS == 'should-not-change'


@patch('open_webui.routers.configs.publish_event', new_callable=AsyncMock)
def test_set_featured_models_rejects_invalid_entry(_publish):
    request = _fake_request(featured_models=[_entry()])

    with pytest.raises(ValidationError, match='provider_name is required'):
        FeaturedModelsForm(FEATURED_MODELS=[_entry(provider_name='')])

    # The existing (valid) list is untouched by the failed validation.
    assert request.app.state.config.FEATURED_MODELS == [_entry()]
