import logging
import os
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends
from fastapi.openapi.utils import get_openapi
from open_webui.models.connector_connections import ConnectorConnections
from open_webui.routers.connectors import GOOGLE_DRIVE_CONNECTOR, get_valid_access_token
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

DRIVE_TOOL_SERVER_ID = 'google_drive_connector'

GOOGLE_DRIVE_FILES_URL = 'https://www.googleapis.com/drive/v3/files'

NOT_CONNECTED_MESSAGE = "Google Drive isn't connected. Ask the user to connect it in Settings → Connectors."

# Google-native files have no bytes to fetch - they must be exported to a plain format instead
GOOGLE_NATIVE_EXPORT_MIME_TYPES = {
    'application/vnd.google-apps.document': 'text/plain',
    'application/vnd.google-apps.spreadsheet': 'text/csv',
    'application/vnd.google-apps.presentation': 'text/plain',
}

# Response is capped so a large exported Doc doesn't blow out the model's context
MAX_RESPONSE_BYTES = 100_000


class DriveConnectionStatusResponse(BaseModel):
    connected: bool
    external_account: str | None = None
    message: str | None = None


class DriveSearchResult(BaseModel):
    id: str
    name: str
    mime_type: str
    modified_time: str | None = None
    web_link: str | None = None


class DriveReadResponse(BaseModel):
    content: str | None = None
    error: str | None = None


@router.get(
    '/drive_connection_status',
    operation_id='drive_connection_status',
    description='Check whether the current user has connected their Google Drive account.',
    response_model=DriveConnectionStatusResponse,
)
async def drive_connection_status(user=Depends(get_verified_user)):
    access_token = await get_valid_access_token(user.id)
    if not access_token:
        return DriveConnectionStatusResponse(connected=False, message=NOT_CONNECTED_MESSAGE)

    connection = await ConnectorConnections.get_by_user_and_connector(user.id, GOOGLE_DRIVE_CONNECTOR)
    return DriveConnectionStatusResponse(
        connected=True,
        external_account=connection.external_account if connection else None,
    )


@router.get(
    '/drive_search',
    operation_id='drive_search',
    description=(
        'Search the current user\'s Google Drive (including shared drives) by file name or content. '
        'Returns matching files with an id, name, MIME type, last modified time and web link. '
        'Use the returned id with drive_read to fetch a file\'s contents.'
    ),
)
async def drive_search(query: str, user=Depends(get_verified_user)) -> dict:
    access_token = await get_valid_access_token(user.id)
    if not access_token:
        return {'connected': False, 'message': NOT_CONNECTED_MESSAGE}

    escaped_query = query.replace("\\", "\\\\").replace("'", "\\'")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            GOOGLE_DRIVE_FILES_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            params={
                'q': f"fullText contains '{escaped_query}' or name contains '{escaped_query}'",
                'includeItemsFromAllDrives': 'true',
                'supportsAllDrives': 'true',
                'fields': 'files(id,name,mimeType,modifiedTime,webViewLink)',
                'pageSize': 10,
            },
        )

    if response.status_code != 200:
        log.error(f'Google Drive search failed: {response.status_code} {response.text}')
        return {'error': 'Failed to search Google Drive.'}

    files = response.json().get('files', [])
    return {
        'results': [
            DriveSearchResult(
                id=f['id'],
                name=f['name'],
                mime_type=f['mimeType'],
                modified_time=f.get('modifiedTime'),
                web_link=f.get('webViewLink'),
            )
            for f in files
        ]
    }


@router.get(
    '/drive_read',
    operation_id='drive_read',
    description=(
        'Read the contents of a Google Drive file by id (as returned by drive_search). '
        'Google Docs/Sheets/Slides are exported to plain text/CSV. Other files (e.g. PDF, DOCX) are '
        'downloaded directly if the owner allows it. Google Vids are not supported yet. '
        'Large files are truncated with a note.'
    ),
    response_model=DriveReadResponse,
)
async def drive_read(file_id: str, user=Depends(get_verified_user)) -> DriveReadResponse:
    access_token = await get_valid_access_token(user.id)
    if not access_token:
        return DriveReadResponse(error=NOT_CONNECTED_MESSAGE)

    headers = {'Authorization': f'Bearer {access_token}'}

    async with httpx.AsyncClient() as client:
        metadata_response = await client.get(
            f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}',
            headers=headers,
            params={'fields': 'mimeType,capabilities(canDownload)', 'supportsAllDrives': 'true'},
        )

        if metadata_response.status_code != 200:
            log.error(f'Google Drive metadata fetch failed: {metadata_response.status_code} {metadata_response.text}')
            return DriveReadResponse(error='Failed to read this file from Google Drive.')

        metadata = metadata_response.json()
        mime_type = metadata['mimeType']

        if mime_type in GOOGLE_NATIVE_EXPORT_MIME_TYPES:
            content_response = await client.get(
                f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}/export',
                headers=headers,
                params={'mimeType': GOOGLE_NATIVE_EXPORT_MIME_TYPES[mime_type]},
            )
        elif mime_type.startswith('application/vnd.google-apps.'):
            return DriveReadResponse(
                error=f"This file's format ({mime_type}) can't be read in the first release."
            )
        else:
            if not metadata.get('capabilities', {}).get('canDownload', True):
                return DriveReadResponse(error='The owner has restricted downloading of this file.')

            content_response = await client.get(
                f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}',
                headers=headers,
                params={'alt': 'media', 'supportsAllDrives': 'true'},
            )

    if content_response.status_code != 200:
        log.error(f'Google Drive content fetch failed: {content_response.status_code} {content_response.text}')
        return DriveReadResponse(error='Failed to read this file from Google Drive.')

    content_bytes = content_response.content
    truncated = len(content_bytes) > MAX_RESPONSE_BYTES
    content = content_bytes[:MAX_RESPONSE_BYTES].decode('utf-8', errors='replace')
    if truncated:
        content += '\n\n[Content truncated - file exceeds the maximum readable size.]'

    return DriveReadResponse(content=content)


@router.get('/openapi.json', include_in_schema=False)
async def drive_tools_openapi():
    return get_openapi(
        title='Google Drive Connector Tools',
        version='1.0.0',
        routes=router.routes,
    )


async def ensure_drive_tool_server_registered(app):
    """Seed the Drive tool server as a Tool Server connection if it isn't registered yet.

    Runs this feature everywhere without an admin having to add it by hand in
    Settings -> Integrations on every environment. Uses a loopback URL (this backend
    calling itself) since PORT is the port this process actually listens on, which
    stays correct regardless of the public hostname (WEBUI_URL) requests come in on.
    """
    connections = list(app.state.config.TOOL_SERVER_CONNECTIONS or [])
    if any((connection.get('info') or {}).get('id') == DRIVE_TOOL_SERVER_ID for connection in connections):
        return

    port = os.getenv('PORT', '8080')
    base_url = os.getenv('CONNECTOR_TOOLS_BASE_URL', f'http://localhost:{port}/api/v1/connectors/tools')

    connections.append(
        {
            'url': base_url,
            'path': 'openapi.json',
            'type': 'openapi',
            'auth_type': 'session',
            'key': '',
            'config': {'enable': True},
            'info': {'id': DRIVE_TOOL_SERVER_ID, 'name': 'Google Drive Connector'},
        }
    )
    app.state.config.TOOL_SERVER_CONNECTIONS = connections
    log.info(f'Registered Google Drive connector tool server at {base_url}')
