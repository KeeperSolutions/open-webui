import asyncio
import logging
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from open_webui.config import (
    GOOGLE_DRIVE_CONNECTOR_CLIENT_ID,
    GOOGLE_DRIVE_CONNECTOR_CLIENT_SECRET,
    GOOGLE_DRIVE_CONNECTOR_REDIRECT_URI,
)
from open_webui.models.connector_connections import ConnectorConnections
from open_webui.utils.auth import (
    create_token,
    decode_token,
    get_verified_user,
    invalidate_token,
    is_valid_token,
)
from pydantic import BaseModel
from starlette.responses import HTMLResponse, RedirectResponse

log = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_DRIVE_CONNECTOR = 'google_drive'
GOOGLE_AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'
GOOGLE_REVOKE_URL = 'https://oauth2.googleapis.com/revoke'

# Refresh a bit before actual expiry to avoid handing out a token that expires mid-request
TOKEN_EXPIRY_BUFFER_SECONDS = 120

_refresh_locks: dict[str, asyncio.Lock] = {}

# Sweep out unheld locks once the table grows past this size
_REFRESH_LOCKS_SWEEP_THRESHOLD = 1000


def _get_refresh_lock(user_id: str) -> asyncio.Lock:
    if len(_refresh_locks) > _REFRESH_LOCKS_SWEEP_THRESHOLD:
        for uid, lock in list(_refresh_locks.items()):
            if not lock.locked():
                del _refresh_locks[uid]

    if user_id not in _refresh_locks:
        _refresh_locks[user_id] = asyncio.Lock()
    return _refresh_locks[user_id]


def _connector_popup_response(message: str = 'You can close this window.') -> HTMLResponse:
    """The connect flow runs in a popup window - close it instead of redirecting the popup itself."""
    return HTMLResponse(f"""<!doctype html>
<html>
<body style="font-family: sans-serif; padding: 2rem;">
<p>{message}</p>
<script>window.close();</script>
</body>
</html>""")


class ConnectorStatusResponse(BaseModel):
    connected: bool
    external_account: str | None = None
    connected_at: int | None = None
    # Whether Google confirmed the token was revoked on disconnect; None when not applicable
    revoked: bool | None = None


@router.get('/google-drive/status', response_model=ConnectorStatusResponse)
async def get_google_drive_status(user=Depends(get_verified_user)):
    connection = await ConnectorConnections.get_by_user_and_connector(user.id, GOOGLE_DRIVE_CONNECTOR)
    if not connection:
        return ConnectorStatusResponse(connected=False)

    return ConnectorStatusResponse(
        connected=True,
        external_account=connection.external_account,
        connected_at=connection.created_at,
    )


@router.get('/google-drive/connect')
async def connect_google_drive(user=Depends(get_verified_user)):
    if not GOOGLE_DRIVE_CONNECTOR_CLIENT_ID.value or not GOOGLE_DRIVE_CONNECTOR_REDIRECT_URI.value:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Google Drive connector is not configured',
        )

    state = create_token({'purpose': 'gdrive_connect', 'user_id': user.id}, expires_delta=timedelta(minutes=10))

    params = {
        'client_id': GOOGLE_DRIVE_CONNECTOR_CLIENT_ID.value,
        'redirect_uri': GOOGLE_DRIVE_CONNECTOR_REDIRECT_URI.value,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/drive.readonly email',
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true',
        'state': state,
    }

    return RedirectResponse(f'{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}')


@router.get('/google-drive/callback')
async def google_drive_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    user=Depends(get_verified_user),
):
    if error:
        # The user declined consent on Google's screen - not an app error, just close the popup
        log.info(f'Google Drive connect declined by user {user.id}: {error}')
        return _connector_popup_response()

    if not code or not state:
        return _connector_popup_response('Invalid or expired connect request.')

    payload = decode_token(state)
    if not payload or payload.get('purpose') != 'gdrive_connect' or payload.get('user_id') != user.id:
        return _connector_popup_response('Invalid or expired connect request.')

    if not await is_valid_token(payload, request.app.state.redis):
        return _connector_popup_response('Invalid or expired connect request.')

    # Burn the state now so it can't be replayed, regardless of whether the exchange below succeeds
    await invalidate_token(request, state)

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                'code': code,
                'client_id': GOOGLE_DRIVE_CONNECTOR_CLIENT_ID.value,
                'client_secret': GOOGLE_DRIVE_CONNECTOR_CLIENT_SECRET.value,
                'redirect_uri': GOOGLE_DRIVE_CONNECTOR_REDIRECT_URI.value,
                'grant_type': 'authorization_code',
            },
        )

        if token_response.status_code != 200:
            log.error(f'Google Drive token exchange failed: {token_response.status_code} {token_response.text}')
            return _connector_popup_response('Failed to connect Google Drive.')

        token_data = token_response.json()

        userinfo_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={'Authorization': f'Bearer {token_data["access_token"]}'},
        )
        external_account = None
        if userinfo_response.status_code == 200:
            external_account = userinfo_response.json().get('email')
        else:
            log.warning(f'Failed to fetch Google Drive userinfo: {userinfo_response.status_code} {userinfo_response.text}')

    await ConnectorConnections.upsert(
        user_id=user.id,
        connector=GOOGLE_DRIVE_CONNECTOR,
        token={
            'access_token': token_data['access_token'],
            'refresh_token': token_data.get('refresh_token'),
            'token_type': token_data.get('token_type', 'Bearer'),
        },
        expires_at=int(time.time()) + token_data.get('expires_in', 3600),
        external_account=external_account,
        scopes=token_data.get('scope'),
    )

    return _connector_popup_response('Google Drive connected. This window will close automatically.')


@router.post('/google-drive/disconnect', response_model=ConnectorStatusResponse)
async def disconnect_google_drive(user=Depends(get_verified_user)):
    connection = await ConnectorConnections.get_by_user_and_connector(user.id, GOOGLE_DRIVE_CONNECTOR)

    revoked = None
    if connection:
        revoke_token = connection.token.get('refresh_token') or connection.token.get('access_token')
        revoked = False
        try:
            async with httpx.AsyncClient() as client:
                revoke_response = await client.post(GOOGLE_REVOKE_URL, params={'token': revoke_token})
            revoked = revoke_response.status_code == 200
            if not revoked:
                log.warning(
                    f'Google Drive revoke returned {revoke_response.status_code} for user {user.id}: {revoke_response.text}'
                )
        except Exception as e:
            log.warning(f'Failed to revoke Google Drive token for user {user.id}: {e}')

        await ConnectorConnections.delete_by_user_and_connector(user.id, GOOGLE_DRIVE_CONNECTOR)

    return ConnectorStatusResponse(connected=False, revoked=revoked)


async def get_valid_access_token(user_id: str) -> str | None:
    """Return a valid Google Drive access token for the user, refreshing it if needed."""
    connection = await ConnectorConnections.get_by_user_and_connector(user_id, GOOGLE_DRIVE_CONNECTOR)
    if not connection:
        return None

    if connection.expires_at - int(time.time()) > TOKEN_EXPIRY_BUFFER_SECONDS:
        return connection.token['access_token']

    async with _get_refresh_lock(user_id):
        # re-fetch in case another request already refreshed it while we waited for the lock
        connection = await ConnectorConnections.get_by_user_and_connector(user_id, GOOGLE_DRIVE_CONNECTOR)
        if not connection:
            return None
        if connection.expires_at - int(time.time()) > TOKEN_EXPIRY_BUFFER_SECONDS:
            return connection.token['access_token']

        refresh_token = connection.token.get('refresh_token')
        if not refresh_token:
            return connection.token['access_token']

        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    'refresh_token': refresh_token,
                    'client_id': GOOGLE_DRIVE_CONNECTOR_CLIENT_ID.value,
                    'client_secret': GOOGLE_DRIVE_CONNECTOR_CLIENT_SECRET.value,
                    'grant_type': 'refresh_token',
                },
            )

        if response.status_code != 200:
            error = response.json().get('error') if response.content else None
            if error == 'invalid_grant':
                log.info(f'Google Drive refresh token invalid for user {user_id}, clearing connection')
                await ConnectorConnections.delete_by_user_and_connector(user_id, GOOGLE_DRIVE_CONNECTOR)
                return None
            log.error(f'Google Drive token refresh failed: {response.status_code} {response.text}')
            return None

        token_data = response.json()
        new_token = {
            'access_token': token_data['access_token'],
            # Google only returns a new refresh_token if it rotated it - keep the old one otherwise
            'refresh_token': token_data.get('refresh_token', refresh_token),
            'token_type': token_data.get('token_type', 'Bearer'),
        }

        await ConnectorConnections.upsert(
            user_id=user_id,
            connector=GOOGLE_DRIVE_CONNECTOR,
            token=new_token,
            expires_at=int(time.time()) + token_data.get('expires_in', 3600),
            external_account=connection.external_account,
            scopes=connection.scopes,
        )

        return new_token['access_token']
