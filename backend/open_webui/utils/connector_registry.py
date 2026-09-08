"""Metadata registry for OAuth-connected connectors (Google Drive, future: Slack, Notion, ...).

This only holds display/UI metadata. Each connector's actual OAuth flow (authorize,
callback, token refresh) stays provider-specific in backend/open_webui/routers/connectors.py -
providers differ too much (scopes, token semantics) to generalize that part.
"""

CONNECTOR_REGISTRY = [
    {
        'id': 'google_drive',
        'name': 'Google Drive',
        'description': 'Search, read, and upload files instantly',
        'icon': 'google_drive',
        'connect_url': '/connectors/google-drive/connect',
    },
]


def get_connector(connector_id: str) -> dict | None:
    return next((c for c in CONNECTOR_REGISTRY if c['id'] == connector_id), None)
