"""A user's own PII masking preference and the instance-wide default under it.

The preference is stored as `pii_masking_enabled` in
`settings.ui.pipelines.valves[<id>]` for every id in `PII_FILTER_IDS`. No stored
boolean means the user follows the instance default.
"""

from typing import Any, Literal, Optional

from open_webui.env import PII_FILTER_IDS

PiiMaskingPreference = Literal['default', 'on', 'off']
PREFERENCES = ('default', 'on', 'off')


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _filter_ids() -> list[str]:
    # Same order as `features.pii_filter_ids`, so frontend and backend agree on
    # which id wins when stored values differ.
    return sorted(PII_FILTER_IDS)


def settings_dict(settings: Any) -> dict:
    """`user.settings` as a plain dict, whatever shape it arrives in."""
    if settings is None:
        return {}
    if isinstance(settings, dict):
        return settings
    try:
        return _mapping(settings.model_dump())
    except Exception:
        return {}


def stored_pii_masking(settings: Any) -> Optional[bool]:
    """The stored boolean for the first PII filter id that has one, else None."""
    ui = _mapping(settings_dict(settings).get('ui'))
    valves = _mapping(_mapping(ui.get('pipelines')).get('valves'))
    for filter_id in _filter_ids():
        value = _mapping(valves.get(filter_id)).get('pii_masking_enabled')
        if isinstance(value, bool):
            return value
    return None


def preference_of(settings: Any) -> PiiMaskingPreference:
    stored = stored_pii_masking(settings)
    if stored is None:
        return 'default'
    return 'on' if stored else 'off'


def effective_pii_masking(settings: Any, instance_default: bool) -> bool:
    """The user's stored choice, or the instance default when they never chose."""
    stored = stored_pii_masking(settings)
    return instance_default if stored is None else stored


def instance_pii_masking_default(request: Any) -> bool:
    """The admin-set instance default. Anything but an explicit False reads as ON."""
    try:
        value = request.app.state.config.PII_MASKING_DEFAULT_ENABLED
    except Exception:
        return True
    return value if isinstance(value, bool) else True


def ui_with_pii_preference(ui: Any, preference: PiiMaskingPreference) -> dict:
    """A copy of `ui` where only `pii_masking_enabled` changed, for every PII filter id.

    `'default'` removes the key, and removes a filter's entry once it is empty.
    """
    out = dict(_mapping(ui))
    pipelines = dict(_mapping(out.get('pipelines')))
    valves = dict(_mapping(pipelines.get('valves')))
    for filter_id in _filter_ids():
        entry = dict(_mapping(valves.get(filter_id)))
        if preference == 'default':
            entry.pop('pii_masking_enabled', None)
        else:
            entry['pii_masking_enabled'] = preference == 'on'
        if entry:
            valves[filter_id] = entry
        else:
            valves.pop(filter_id, None)
    pipelines['valves'] = valves
    out['pipelines'] = pipelines
    return out


def ui_keeping_stored_pii(incoming_ui: Any, stored_settings: Any) -> dict:
    """`incoming_ui` with the masking preference reset to what is stored.

    The generic settings route replaces the whole `ui` object, and the frontend
    sends whatever its store holds. Without this, a tab opened before an admin
    changed the preference would write the old value back on any save.
    """
    return ui_with_pii_preference(incoming_ui, preference_of(stored_settings))
