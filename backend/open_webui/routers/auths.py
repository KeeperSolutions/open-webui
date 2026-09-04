from __future__ import annotations

import asyncio
import datetime
import logging
import re
import time
import urllib
import uuid
from ssl import CERT_NONE, CERT_REQUIRED, PROTOCOL_TLS
from typing import Optional

from aiohttp import BasicAuth, ClientSession
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from ldap3 import NONE, Connection, Server, Tls
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn
from open_webui.config import (
    ENABLE_PASSWORD_AUTH,
    OAUTH_PROVIDERS,
)
from open_webui.constants import ERROR_MESSAGES, WEBHOOK_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_SSL,
    ENABLE_INITIAL_ADMIN_SIGNUP,
    ENABLE_OAUTH_TOKEN_EXCHANGE,
    OAUTH_TOKEN_EXCHANGE_RATE_LIMIT,
    OAUTH_TOKEN_EXCHANGE_RATE_LIMIT_WINDOW,
    OAUTH_TOKEN_EXCHANGE_TRUSTED_CLIENT_IDS,
    WEBUI_AUTH,
    WEBUI_AUTH_COOKIE_SAME_SITE,
    WEBUI_AUTH_COOKIE_SECURE,
    WEBUI_AUTH_SIGNOUT_REDIRECT_URL,
    WEBUI_AUTH_TRUSTED_EMAIL_HEADER,
    WEBUI_AUTH_TRUSTED_GROUPS_HEADER,
    WEBUI_AUTH_TRUSTED_NAME_HEADER,
    WEBUI_AUTH_TRUSTED_ROLE_HEADER,
)
from open_webui.internal.db import get_async_session
from open_webui.models.auths import (
    AddUserForm,
    ApiKey,
    Auths,
    LdapForm,
    SigninForm,
    SigninResponse,
    SignupForm,
    Token,
    UpdatePasswordForm,
)
from open_webui.models.groups import Groups
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.users import (
    UpdateProfileForm,
    UserModel,
    UserProfileImageResponse,
    Users,
    UserStatus,
)
from open_webui.utils.access_control import get_permissions, has_permission
from open_webui.utils.auth import (
    create_api_key,
    create_token,
    decode_token,
    get_admin_user,
    get_current_user,
    get_http_authorization_cred,
    get_password_hash,
    get_verified_user,
    invalidate_token,
    validate_password,
    verify_password,
)
from open_webui.utils.groups import apply_default_group_assignment
from open_webui.utils.misc import parse_duration, validate_email_format
from open_webui.utils.oauth import auth_manager_config
from open_webui.utils.rate_limit import RateLimiter
from open_webui.utils.redis import get_redis_client
from open_webui.utils.webhook import post_webhook
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

log = logging.getLogger(__name__)

# Forgive us our failed attempts, as we forgive those
# who exceed their allotted rate against this gate.
signin_rate_limiter = RateLimiter(redis_client=get_redis_client(), limit=5 * 3, window=60 * 3)
# Best-effort throttle only: there is no caller identity before the provider answers,
# and deployments may derive request.client from proxy headers.
token_exchange_rate_limiter = (
    RateLimiter(
        redis_client=get_redis_client(),
        limit=OAUTH_TOKEN_EXCHANGE_RATE_LIMIT,
        window=OAUTH_TOKEN_EXCHANGE_RATE_LIMIT_WINDOW,
    )
    if OAUTH_TOKEN_EXCHANGE_RATE_LIMIT is not None
    else None
)


ADMIN_CONFIG_KEYS = {
    'SHOW_ADMIN_DETAILS': 'auth.admin.show',
    'ADMIN_EMAIL': 'auth.admin.email',
    'WEBUI_URL': 'webui.url',
    'ENABLE_SIGNUP': 'ui.enable_signup',
    'ENABLE_API_KEYS': 'auth.enable_api_keys',
    'ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS': 'auth.api_key.endpoint_restrictions',
    'API_KEYS_ALLOWED_ENDPOINTS': 'auth.api_key.allowed_endpoints',
    'DEFAULT_USER_ROLE': 'ui.default_user_role',
    'DEFAULT_GROUP_ID': 'ui.default_group_id',
    'JWT_EXPIRES_IN': 'auth.jwt_expiry',
    'ENABLE_COMMUNITY_SHARING': 'ui.enable_community_sharing',
    'ENABLE_MESSAGE_RATING': 'ui.enable_message_rating',
    'ENABLE_FOLDERS': 'folders.enable',
    'FOLDER_MAX_FILE_COUNT': 'folders.max_file_count',
    'AUTOMATION_MAX_COUNT': 'automations.max_count',
    'AUTOMATION_MIN_INTERVAL': 'automations.min_interval',
    'ENABLE_AUTOMATIONS': 'automations.enable',
    'ENABLE_CHANNELS': 'channels.enable',
    'CHANNEL_MODEL_RESPONSE_MODE': 'channels.model_response_mode',
    'ENABLE_CALENDAR': 'calendar.enable',
    'ENABLE_MEMORIES': 'memories.enable',
    'ENABLE_MEMORY_SYSTEM_CONTEXT': 'memories.system_context.enable',
    'ENABLE_NOTES': 'notes.enable',
    'ENABLE_USER_WEBHOOKS': 'ui.enable_user_webhooks',
    'ENABLE_USER_STATUS': 'users.enable_status',
    'PENDING_USER_OVERLAY_TITLE': 'ui.pending_user_overlay_title',
    'PENDING_USER_OVERLAY_CONTENT': 'ui.pending_user_overlay_content',
    'RESPONSE_WATERMARK': 'ui.watermark',
}

# form-field name → fork ConfigVar attribute name (Risk #1: no dotted keys)
LDAP_SERVER_CONFIG_KEYS = {
    'label': 'LDAP_SERVER_LABEL',
    'host': 'LDAP_SERVER_HOST',
    'port': 'LDAP_SERVER_PORT',
    'attribute_for_mail': 'LDAP_ATTRIBUTE_FOR_MAIL',
    'attribute_for_username': 'LDAP_ATTRIBUTE_FOR_USERNAME',
    'app_dn': 'LDAP_APP_DN',
    'app_dn_password': 'LDAP_APP_PASSWORD',
    'search_base': 'LDAP_SEARCH_BASE',
    'search_filters': 'LDAP_SEARCH_FILTERS',
    'use_tls': 'LDAP_USE_TLS',
    'certificate_path': 'LDAP_CA_CERT_FILE',
    'validate_cert': 'LDAP_VALIDATE_CERT',
    'ciphers': 'LDAP_CIPHERS',
    'enable_group_management': 'ENABLE_LDAP_GROUP_MANAGEMENT',
    'enable_group_creation': 'ENABLE_LDAP_GROUP_CREATION',
    'attribute_for_groups': 'LDAP_ATTRIBUTE_FOR_GROUPS',
}


async def get_config_values(key_map: dict[str, str]) -> dict:
    from open_webui import config as _cfg

    return {field: getattr(getattr(_cfg, field, None), 'value', None) for field in key_map}


def config_updates(data: dict, key_map: dict[str, str]) -> dict:
    from open_webui import config as _cfg

    for field, value in data.items():
        if field in key_map:
            cv = getattr(_cfg, field, None)
            if cv is not None:
                cv.value = value
                cv.commit()
    return {}


async def create_session_response(
    request: Request,
    user,
    db,
    response: Response = None,
    set_cookie: bool = False,
    source: str = 'api',
) -> dict:
    """
    Create JWT token and build session response for a user.
    Shared helper for signin, signup, ldap_auth, add_user, and token_exchange endpoints.

    Args:
        request: FastAPI request object
        user: User object
        db: Database session
        response: FastAPI response object (required if set_cookie is True)
        set_cookie: Whether to set the auth cookie on the response
    """
    expires_delta = parse_duration(request.app.state.config.JWT_EXPIRES_IN)
    expires_at = None
    if expires_delta:
        expires_at = int(time.time()) + int(expires_delta.total_seconds())

    token = create_token(
        data={'id': user.id},
        expires_delta=expires_delta,
    )

    if set_cookie and response:
        datetime_expires_at = datetime.datetime.fromtimestamp(expires_at, datetime.timezone.utc) if expires_at else None
        max_age = int(expires_delta.total_seconds()) if expires_delta else None
        response.set_cookie(
            key='token',
            value=token,
            expires=datetime_expires_at,
            httponly=True,
            samesite=WEBUI_AUTH_COOKIE_SAME_SITE,
            secure=WEBUI_AUTH_COOKIE_SECURE,
            **({'max_age': max_age} if max_age is not None else {}),
        )

    user_permissions = await get_permissions(user.id, request.app.state.config.USER_PERMISSIONS, db=db)

    return {
        'token': token,
        'token_type': 'Bearer',
        'expires_at': expires_at,
        'id': user.id,
        'email': user.email,
        'name': user.name,
        'role': user.role,
        'profile_image_url': f'/api/v1/users/{user.id}/profile/image',
        'permissions': user_permissions,
    }


############################
# GetSessionUser
############################


class SessionUserResponse(Token, UserProfileImageResponse):
    expires_at: int | None = None
    permissions: dict | None = None


class SessionUserInfoResponse(SessionUserResponse, UserStatus):
    bio: str | None = None
    gender: str | None = None
    date_of_birth: datetime.date | None = None


@router.get('/', response_model=SessionUserInfoResponse)
async def get_session_user(
    request: Request,
    response: Response,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    token = None
    auth_header = request.headers.get('Authorization')
    if auth_header:
        auth_token = get_http_authorization_cred(auth_header)
        if auth_token is not None:
            token = auth_token.credentials
    if token is None:
        token = request.cookies.get('token')
    if token is None and getattr(request.state, 'token', None):
        token = request.state.token.credentials
    data = decode_token(token) if token else None

    expires_at = None

    if data:
        expires_at = data.get('exp')

        if (expires_at is not None) and int(time.time()) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.INVALID_TOKEN,
            )

        # Set the cookie token
        max_age = int(expires_at - time.time()) if expires_at else None
        response.set_cookie(
            key='token',
            value=token,
            expires=(datetime.datetime.fromtimestamp(expires_at, datetime.timezone.utc) if expires_at else None),
            httponly=True,  # Ensures the cookie is not accessible via JavaScript
            samesite=WEBUI_AUTH_COOKIE_SAME_SITE,
            secure=WEBUI_AUTH_COOKIE_SECURE,
            **({'max_age': max_age} if max_age is not None else {}),
        )

    user_permissions = await get_permissions(user.id, request.app.state.config.USER_PERMISSIONS, db=db)

    response_data = {
        'token': token,
        'token_type': 'Bearer',
        'expires_at': expires_at,
        'id': user.id,
        'email': user.email,
        'name': user.name,
        'role': user.role,
        'profile_image_url': user.profile_image_url,
        'bio': user.bio,
        'gender': user.gender,
        'date_of_birth': user.date_of_birth,
        'status_emoji': user.status_emoji,
        'status_message': user.status_message,
        'status_expires_at': user.status_expires_at,
        'permissions': user_permissions,
    }

    return response_data


############################
# Update Profile
############################


@router.post('/update/profile', response_model=UserProfileImageResponse)
async def update_profile(
    request: Request,
    form_data: UpdateProfileForm,
    session_user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if session_user:
        user = await Users.update_user_by_id(
            session_user.id,
            form_data.model_dump(),
            db=db,
        )
        if user:
            await publish_event(
                request,
                EVENTS.USER_PROFILE_UPDATED,
                actor=session_user,
                subject_id=session_user.id,
                data={'updated_fields': list(form_data.model_dump().keys())},
            )
            return user
        else:
            raise HTTPException(400, detail=ERROR_MESSAGES.DEFAULT())
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


############################
# Update Timezone
############################


class UpdateTimezoneForm(BaseModel):
    timezone: str


@router.post('/update/timezone')
async def update_timezone(
    request: Request,
    form_data: UpdateTimezoneForm,
    session_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    if session_user:
        await Users.update_user_by_id(
            session_user.id,
            {'timezone': form_data.timezone},
            db=db,
        )
        await publish_event(
            request,
            EVENTS.USER_UPDATED,
            actor=session_user,
            subject_id=session_user.id,
            data={'updated_fields': ['timezone']},
        )
        return {'status': True}
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


############################
# Update Password
############################


@router.post('/update/password', response_model=bool)
async def update_password(
    request: Request,
    form_data: UpdatePasswordForm,
    session_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    # Trusted-header auth mode delegates passwords to the reverse proxy
    if WEBUI_AUTH_TRUSTED_EMAIL_HEADER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.ACTION_PROHIBITED)
    if session_user:
        user = await Auths.authenticate_user(
            session_user.email,
            lambda pw: verify_password(form_data.password, pw),
            db=db,
        )

        if user:
            try:
                validate_password(form_data.new_password)
            except Exception as e:
                raise HTTPException(400, detail=str(e))
            hashed = await get_password_hash(form_data.new_password)
            success = await Auths.update_user_password_by_id(user.id, hashed, db=db)
            if success:
                await publish_event(
                    request,
                    EVENTS.AUTH_PASSWORD_CHANGED,
                    actor=user,
                    subject_id=user.id,
                    subject_type='user',
                )
            return success
        else:
            raise HTTPException(400, detail=ERROR_MESSAGES.INCORRECT_PASSWORD)
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


def _unescape_ldap_dn_value(value: str) -> str:
    """Resolve RFC 4514 escapes in a DN value, e.g. ``CN=Sales\\, EMEA`` -> ``Sales, EMEA``.

    Consecutive ``\\XX`` hex escapes encode UTF-8 bytes and are decoded together.
    """
    hexdigits = '0123456789abcdefABCDEF'
    result = []
    pos = 0
    length = len(value)
    while pos < length:
        char = value[pos]
        if char == '\\' and pos + 1 < length:
            if pos + 2 < length and value[pos + 1] in hexdigits and value[pos + 2] in hexdigits:
                byte_values = bytearray()
                while (
                    pos + 2 < length
                    and value[pos] == '\\'
                    and value[pos + 1] in hexdigits
                    and value[pos + 2] in hexdigits
                ):
                    byte_values.append(int(value[pos + 1 : pos + 3], 16))
                    pos += 3
                result.append(byte_values.decode('utf-8', errors='replace'))
            else:
                # Backslash escaping a literal special char, e.g. "\," or "\+".
                result.append(value[pos + 1])
                pos += 2
        else:
            result.append(char)
            pos += 1
    return ''.join(result)


def extract_group_cn_from_dn(group_dn: str) -> str | None:
    """Return the first CN component of an LDAP group DN, or None.

    Uses ``parse_dn`` so escaped separators inside a value (e.g. a group whose
    name contains a comma) are handled correctly instead of naively splitting
    on ``,``.
    """
    for attr_type, attr_value, _ in parse_dn(group_dn):
        if attr_type.upper() == 'CN':
            return _unescape_ldap_dn_value(attr_value)
    return None


############################
# LDAP Authentication
############################
@router.post('/ldap', response_model=SessionUserResponse)
async def ldap_auth(
    request: Request,
    response: Response,
    form_data: LdapForm,
    db: AsyncSession = Depends(get_async_session),
):
    # Security checks FIRST - before loading any config
    if not request.app.state.config.ENABLE_LDAP:
        raise HTTPException(400, detail='LDAP authentication is not enabled')

    if not ENABLE_PASSWORD_AUTH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACTION_PROHIBITED,
        )

    # Reject empty passwords before attempting the LDAP bind.
    # Per RFC 4513 §5.1.2, a Simple Bind with a non-empty DN but empty
    # password is "unauthenticated simple authentication" — many LDAP
    # servers (OpenLDAP default, some AD configs) return success for these,
    # which would grant access without valid credentials.
    if not form_data.password or not form_data.password.strip():
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)

    # NOW load LDAP config variables
    LDAP_SERVER_LABEL = request.app.state.config.LDAP_SERVER_LABEL
    LDAP_SERVER_HOST = request.app.state.config.LDAP_SERVER_HOST
    LDAP_SERVER_PORT = request.app.state.config.LDAP_SERVER_PORT
    LDAP_ATTRIBUTE_FOR_MAIL = request.app.state.config.LDAP_ATTRIBUTE_FOR_MAIL
    LDAP_ATTRIBUTE_FOR_USERNAME = request.app.state.config.LDAP_ATTRIBUTE_FOR_USERNAME
    LDAP_SEARCH_BASE = request.app.state.config.LDAP_SEARCH_BASE
    LDAP_SEARCH_FILTERS = request.app.state.config.LDAP_SEARCH_FILTERS
    LDAP_APP_DN = request.app.state.config.LDAP_APP_DN
    LDAP_APP_PASSWORD = request.app.state.config.LDAP_APP_PASSWORD
    LDAP_USE_TLS = request.app.state.config.LDAP_USE_TLS
    LDAP_CA_CERT_FILE = request.app.state.config.LDAP_CA_CERT_FILE
    LDAP_VALIDATE_CERT = CERT_REQUIRED if request.app.state.config.LDAP_VALIDATE_CERT else CERT_NONE
    LDAP_CIPHERS = request.app.state.config.LDAP_CIPHERS if request.app.state.config.LDAP_CIPHERS else 'ALL'

    try:
        tls = Tls(
            validate=LDAP_VALIDATE_CERT,
            version=PROTOCOL_TLS,
            ca_certs_file=LDAP_CA_CERT_FILE,
            ciphers=LDAP_CIPHERS,
        )
    except Exception as e:
        log.error(f'TLS configuration error: {str(e)}')
        raise HTTPException(400, detail='Failed to configure TLS for LDAP connection.')

    try:
        server = Server(
            host=LDAP_SERVER_HOST,
            port=LDAP_SERVER_PORT,
            get_info=NONE,
            use_ssl=LDAP_USE_TLS,
            tls=tls,
        )
        connection_app = Connection(
            server,
            LDAP_APP_DN,
            LDAP_APP_PASSWORD,
            auto_bind='NONE',
            authentication='SIMPLE' if LDAP_APP_DN else 'ANONYMOUS',
        )
        if not await asyncio.to_thread(connection_app.bind):
            raise HTTPException(400, detail='Application account bind failed')

        ENABLE_LDAP_GROUP_MANAGEMENT = request.app.state.config.ENABLE_LDAP_GROUP_MANAGEMENT
        ENABLE_LDAP_GROUP_CREATION = request.app.state.config.ENABLE_LDAP_GROUP_CREATION
        LDAP_ATTRIBUTE_FOR_GROUPS = request.app.state.config.LDAP_ATTRIBUTE_FOR_GROUPS

        search_attributes = [
            f'{LDAP_ATTRIBUTE_FOR_USERNAME}',
            f'{LDAP_ATTRIBUTE_FOR_MAIL}',
            'cn',
        ]
        if ENABLE_LDAP_GROUP_MANAGEMENT:
            search_attributes.append(f'{LDAP_ATTRIBUTE_FOR_GROUPS}')
            log.info(f'LDAP Group Management enabled. Adding {LDAP_ATTRIBUTE_FOR_GROUPS} to search attributes')
        log.info(f'LDAP search attributes: {search_attributes}')

        search_success = await asyncio.to_thread(
            connection_app.search,
            search_base=LDAP_SEARCH_BASE,
            search_filter=f'(&({LDAP_ATTRIBUTE_FOR_USERNAME}={escape_filter_chars(form_data.user.lower())}){LDAP_SEARCH_FILTERS})',
            attributes=search_attributes,
        )
        if not search_success or not connection_app.entries:
            raise HTTPException(400, detail='User not found in the LDAP server')

        entry = connection_app.entries[0]
        entry_username = entry[f'{LDAP_ATTRIBUTE_FOR_USERNAME}'].value
        email = entry[f'{LDAP_ATTRIBUTE_FOR_MAIL}'].value  # retrieve the Attribute value

        username_list = []  # list of usernames from LDAP attribute
        if isinstance(entry_username, list):
            username_list = [str(name).lower() for name in entry_username]
        else:
            username_list = [str(entry_username).lower()]

        # TODO: support multiple emails if LDAP returns a list
        if not email:
            raise HTTPException(400, 'User does not have a valid email address.')
        elif isinstance(email, str):
            email = email.lower()
        elif isinstance(email, list):
            email = email[0].lower()
        else:
            email = str(email).lower()

        cn = str(entry['cn'])  # common name
        user_dn = entry.entry_dn  # user distinguished name

        user_groups = []
        if ENABLE_LDAP_GROUP_MANAGEMENT and LDAP_ATTRIBUTE_FOR_GROUPS in entry:
            group_dns = entry[LDAP_ATTRIBUTE_FOR_GROUPS]
            log.info(f'LDAP raw group DNs for user {username_list}: {group_dns}')

            if group_dns:
                log.info(f'LDAP group_dns original: {group_dns}')
                log.info(f'LDAP group_dns type: {type(group_dns)}')
                log.info(f'LDAP group_dns length: {len(group_dns)}')

                if hasattr(group_dns, 'value'):
                    group_dns = group_dns.value
                    log.info(f'Extracted .value property: {group_dns}')
                elif hasattr(group_dns, '__iter__') and not isinstance(group_dns, (str, bytes)):
                    group_dns = list(group_dns)
                    log.info(f'Converted to list: {group_dns}')

                if isinstance(group_dns, list):
                    group_dns = [str(item) for item in group_dns]
                else:
                    group_dns = [str(group_dns)]

                log.info(f'LDAP group_dns after processing - type: {type(group_dns)}, length: {len(group_dns)}')

                for group_idx, group_dn in enumerate(group_dns):
                    group_dn = str(group_dn)
                    log.info(f'Processing group DN #{group_idx + 1}: {group_dn}')

                    try:
                        group_cn = extract_group_cn_from_dn(group_dn)

                        if group_cn:
                            user_groups.append(group_cn)
                        else:
                            log.warning(f'Could not extract CN from group DN: {group_dn}')
                    except Exception as e:
                        log.warning(f'Failed to extract group name from DN {group_dn}: {e}')

                log.info(f'LDAP groups for user {username_list}: {user_groups} (total: {len(user_groups)})')
            else:
                log.info(f'No groups found for user {username_list}')
        elif ENABLE_LDAP_GROUP_MANAGEMENT:
            log.warning(
                f'LDAP Group Management enabled but {LDAP_ATTRIBUTE_FOR_GROUPS} attribute not found in user entry'
            )

        if username_list and form_data.user.lower() in username_list:
            connection_user = Connection(
                server,
                user_dn,
                form_data.password,
                auto_bind='NONE',
                authentication='SIMPLE',
            )
            if not await asyncio.to_thread(connection_user.bind):
                raise HTTPException(400, 'Authentication failed.')

            user = await Users.get_user_by_email(email, db=db)
            if not user:
                try:
                    # Insert with default role first to avoid TOCTOU race on
                    # first-user registration.  Matches signup_handler pattern.
                    user = await Auths.insert_new_auth(
                        email=email,
                        password=str(uuid.uuid4()),
                        name=cn,
                        role=request.app.state.config.DEFAULT_USER_ROLE,
                        db=db,
                    )

                    if not user:
                        raise HTTPException(500, detail=ERROR_MESSAGES.CREATE_USER_ERROR)

                    # Atomically check if this is the only user *after* the
                    # insert.  Only the single user present should become admin.
                    if await Users.get_num_users(db=db) == 1:
                        await Users.update_user_role_by_id(user.id, 'admin', db=db)
                        user = await Users.get_user_by_id(user.id, db=db)

                    await apply_default_group_assignment(
                        request.app.state.config.DEFAULT_GROUP_ID,
                        user.id,
                        db=db,
                    )

                    if request.app.state.config.WEBHOOK_URL:
                        await post_webhook(
                            request.app.state.WEBUI_NAME,
                            request.app.state.config.WEBHOOK_URL,
                            WEBHOOK_MESSAGES.USER_SIGNUP(user.name),
                            {
                                'action': 'signup',
                                'message': WEBHOOK_MESSAGES.USER_SIGNUP(user.name),
                                'user': user.model_dump_json(exclude_none=True),
                            },
                        )
                    await publish_event(
                        request,
                        EVENTS.USER_CREATED,
                        actor=user,
                        subject_id=user.id,
                        source='ldap',
                        data={'role': user.role},
                    )

                except HTTPException:
                    raise
                except Exception as err:
                    log.error(f'LDAP user creation error: {str(err)}')
                    raise HTTPException(500, detail='Internal error occurred during LDAP user creation.')

            user = await Auths.authenticate_user_by_email(email, db=db)

            if user:
                if ENABLE_LDAP_GROUP_MANAGEMENT and user_groups:
                    try:
                        if ENABLE_LDAP_GROUP_CREATION:
                            await Groups.create_groups_by_group_names(user.id, user_groups, db=db)
                        await Groups.sync_groups_by_group_names(user.id, user_groups, db=db)
                        log.info(f'Successfully synced groups for user {user.id}: {user_groups}')
                    except Exception as e:
                        log.error(f'Failed to sync groups for user {user.id}: {e}')

                return await create_session_response(request, user, db, response, set_cookie=True, source='ldap')
            else:
                raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)
        else:
            raise HTTPException(400, 'User record mismatch.')
    except Exception as e:
        log.error(f'LDAP authentication error: {str(e)}')
        raise HTTPException(400, detail='LDAP authentication failed.')


############################
# SignIn
############################


@router.post('/signin', response_model=SessionUserResponse)
async def signin(
    request: Request,
    response: Response,
    form_data: SigninForm,
    db: AsyncSession = Depends(get_async_session),
):
    if not ENABLE_PASSWORD_AUTH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACTION_PROHIBITED,
        )

    auth_source = 'password'

    if WEBUI_AUTH_TRUSTED_EMAIL_HEADER:
        auth_source = 'trusted_header'
        if WEBUI_AUTH_TRUSTED_EMAIL_HEADER not in request.headers:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.INVALID_TRUSTED_HEADER)

        email = request.headers[WEBUI_AUTH_TRUSTED_EMAIL_HEADER].lower()
        name = email

        if WEBUI_AUTH_TRUSTED_NAME_HEADER:
            name = request.headers.get(WEBUI_AUTH_TRUSTED_NAME_HEADER, email)
            try:
                name = urllib.parse.unquote(name, encoding='utf-8')
            except Exception as e:
                pass

        if not await Users.get_user_by_email(email.lower(), db=db):
            try:
                await signup_handler(
                    request,
                    email,
                    str(uuid.uuid4()),
                    name,
                    db=db,
                    source='trusted_header',
                )
            except IntegrityError:
                if not await Users.get_user_by_email(email.lower(), db=db):
                    raise

        user = await Auths.authenticate_user_by_email(email, db=db)
        if user:
            if WEBUI_AUTH_TRUSTED_GROUPS_HEADER:
                group_names = request.headers.get(WEBUI_AUTH_TRUSTED_GROUPS_HEADER, '').split(',')
                group_names = [name.strip() for name in group_names if name.strip()]

                if group_names:
                    await Groups.sync_groups_by_group_names(user.id, group_names, db=db)

            if WEBUI_AUTH_TRUSTED_ROLE_HEADER:
                trusted_role = request.headers.get(WEBUI_AUTH_TRUSTED_ROLE_HEADER, '').lower().strip()
                if trusted_role in {'admin', 'user', 'pending'}:
                    if user.role != trusted_role:
                        await Users.update_user_role_by_id(user.id, trusted_role, db=db)
                elif trusted_role:
                    log.warning(f'Ignoring invalid trusted role header value: {trusted_role}')

    elif WEBUI_AUTH == False:
        auth_source = 'system'
        admin_email = 'admin@localhost'
        admin_password = 'admin'

        if await Users.get_user_by_email(admin_email.lower(), db=db):
            user = await Auths.authenticate_user(
                admin_email.lower(),
                lambda pw: verify_password(admin_password, pw),
                db=db,
            )
        else:
            if await Users.has_users(db=db):
                raise HTTPException(400, detail=ERROR_MESSAGES.EXISTING_USERS)

            await signup_handler(
                request,
                admin_email,
                admin_password,
                'User',
                db=db,
                source='system',
            )

            user = await Auths.authenticate_user(
                admin_email.lower(),
                lambda pw: verify_password(admin_password, pw),
                db=db,
            )
    else:
        if signin_rate_limiter.is_limited(form_data.email.lower()):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=ERROR_MESSAGES.RATE_LIMIT_EXCEEDED,
            )

        password_bytes = form_data.password.encode('utf-8')
        if len(password_bytes) > 72:
            # TODO: Implement other hashing algorithms that support longer passwords
            log.info('Password too long, truncating to 72 bytes for bcrypt')
            password_bytes = password_bytes[:72]

            # decode safely — ignore incomplete UTF-8 sequences
            form_data.password = password_bytes.decode('utf-8', errors='ignore')

        user = await Auths.authenticate_user(
            form_data.email.lower(),
            lambda pw: verify_password(form_data.password, pw),
            db=db,
        )

    if user:
        return await create_session_response(request, user, db, response, set_cookie=True, source=auth_source)
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


############################
# SignUp
############################


async def signup_handler(
    request: Request,
    email: str,
    password: str,
    name: str,
    profile_image_url: str = '/user.png',
    *,
    db: AsyncSession,
    source: str = 'api',
) -> UserModel:
    """
    Core user-creation logic shared by the signup endpoint and
    trusted-header / no-auth auto-registration flows.

    Returns the newly created UserModel.
    Raises HTTPException on failure.
    """
    # Insert with default role first to avoid TOCTOU race on first signup.
    # If has_users() is checked before insert, concurrent requests during
    # first-user registration can all see an empty table and each get admin.
    hashed = await get_password_hash(password)

    user = await Auths.insert_new_auth(
        email=email.lower(),
        password=hashed,
        name=name,
        profile_image_url=profile_image_url,
        role=request.app.state.config.DEFAULT_USER_ROLE,
        db=db,
    )
    if not user:
        raise HTTPException(500, detail=ERROR_MESSAGES.CREATE_USER_ERROR)

    # Atomically check if this is the only user *after* the insert.
    # Only the single user present at this point should become admin.
    if await Users.get_num_users(db=db) == 1:
        await Users.update_user_role_by_id(user.id, 'admin', db=db)
        user = await Users.get_user_by_id(user.id, db=db)
        request.app.state.config.ENABLE_SIGNUP = False

    if request.app.state.config.WEBHOOK_URL:
        await post_webhook(
            request.app.state.WEBUI_NAME,
            request.app.state.config.WEBHOOK_URL,
            WEBHOOK_MESSAGES.USER_SIGNUP(user.name),
            {
                'action': 'signup',
                'message': WEBHOOK_MESSAGES.USER_SIGNUP(user.name),
                'user': user.model_dump_json(exclude_none=True),
            },
        )

    await apply_default_group_assignment(
        request.app.state.config.DEFAULT_GROUP_ID,
        user.id,
        db=db,
    )

    await publish_event(
        request,
        EVENTS.USER_CREATED,
        actor=user,
        subject_id=user.id,
        source=source,
        data={'role': user.role},
    )

    return user


@router.post('/signup', response_model=SessionUserResponse)
async def signup(
    request: Request,
    response: Response,
    form_data: SignupForm,
    db: AsyncSession = Depends(get_async_session),
):
    has_users = await Users.has_users(db=db)

    if WEBUI_AUTH:
        if has_users:
            if not request.app.state.config.ENABLE_SIGNUP or not request.app.state.config.ENABLE_LOGIN_FORM:
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
        # Don't gate the first admin on ENABLE_SIGNUP: it auto-disables and can persist stale across a DB reset.
        elif not request.app.state.config.ENABLE_LOGIN_FORM and not ENABLE_INITIAL_ADMIN_SIGNUP:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    else:
        if has_users:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)

    if not validate_email_format(form_data.email.lower()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.INVALID_EMAIL_FORMAT)

    if await Users.get_user_by_email(form_data.email.lower(), db=db):
        raise HTTPException(400, detail=ERROR_MESSAGES.EMAIL_TAKEN)

    try:
        try:
            validate_password(form_data.password)
        except Exception as e:
            raise HTTPException(400, detail=str(e))

        user = await signup_handler(
            request,
            form_data.email,
            form_data.password,
            form_data.name,
            form_data.profile_image_url,
            db=db,
        )
        # Auto-onboard user for billing in the background so signup isn't blocked by Stripe latency
        from open_webui.routers.billing import auto_onboard_user
        import asyncio

        async def _onboard():
            try:
                await auto_onboard_user(user, request)
            except Exception as e:
                log.warning(f'Could not auto-onboard user {user.email} for billing: {e}')

        asyncio.create_task(_onboard())

        await publish_event(
            request,
            EVENTS.AUTH_SIGNUP,
            actor=user,
            subject_id=user.id,
            subject_type='user',
            data={'email': user.email},
        )
        return await create_session_response(request, user, db, response, set_cookie=True)
    except HTTPException:
        raise
    except Exception as err:
        log.error(f'Signup error: {str(err)}')
        raise HTTPException(500, detail='An internal error occurred during signup.')


@router.post('/signout')
async def signout(request: Request, response: Response, db: AsyncSession = Depends(get_async_session)):
    # get auth token from headers or cookies
    token = None
    auth_header = request.headers.get('Authorization')
    if auth_header:
        auth_cred = get_http_authorization_cred(auth_header)
        if auth_cred is not None:
            token = auth_cred.credentials
    if token is None:
        token = request.cookies.get('token')

    if token:
        actor = None
        data = decode_token(token)
        if data and data.get('id'):
            actor = await Users.get_user_by_id(data['id'], db=db)
        await invalidate_token(request, token)
        await publish_event(
            request,
            EVENTS.AUTH_LOGOUT,
            actor=actor,
            subject_id=actor.id if actor else None,
            subject_type='user' if actor else None,
        )

    response.delete_cookie('token')
    response.delete_cookie('oui-session')
    response.delete_cookie('oauth_id_token')

    oauth_session_id = request.cookies.get('oauth_session_id')
    if oauth_session_id:
        response.delete_cookie('oauth_session_id')

        session = await OAuthSessions.get_session_by_id(oauth_session_id, db=db)

        # If a custom end_session_endpoint is configured (e.g. AWS Cognito), redirect
        # there directly instead of attempting OIDC discovery.
        openid_end_session_endpoint = request.app.state.config.OPENID_END_SESSION_ENDPOINT
        if openid_end_session_endpoint:
            return JSONResponse(
                status_code=200,
                content={
                    'status': True,
                    'redirect_url': openid_end_session_endpoint,
                },
                headers=response.headers,
            )

        openid_provider_url = request.app.state.config.OPENID_PROVIDER_URL
        oauth_server_metadata_url = (
            request.app.state.oauth_manager.get_server_metadata_url(session.provider) if session else None
        ) or openid_provider_url

        if session and oauth_server_metadata_url:
            oauth_id_token = session.token.get('id_token')
            try:
                async with ClientSession(trust_env=True) as session:
                    async with session.get(oauth_server_metadata_url, ssl=AIOHTTP_CLIENT_SESSION_SSL) as r:
                        if r.status == 200:
                            openid_data = await r.json()
                            logout_url = openid_data.get('end_session_endpoint')

                            if logout_url:
                                return JSONResponse(
                                    status_code=200,
                                    content={
                                        'status': True,
                                        'redirect_url': f'{logout_url}?id_token_hint={oauth_id_token}'
                                        + (
                                            f'&post_logout_redirect_uri={WEBUI_AUTH_SIGNOUT_REDIRECT_URL}'
                                            if WEBUI_AUTH_SIGNOUT_REDIRECT_URL
                                            else ''
                                        ),
                                    },
                                    headers=response.headers,
                                )
                        else:
                            raise Exception('Failed to fetch OpenID configuration')

            except Exception as e:
                log.error(f'OpenID signout error: {str(e)}')
                raise HTTPException(
                    status_code=500,
                    detail='Failed to sign out from the OpenID provider.',
                    headers=response.headers,
                )

    if WEBUI_AUTH_SIGNOUT_REDIRECT_URL:
        return JSONResponse(
            status_code=200,
            content={
                'status': True,
                'redirect_url': WEBUI_AUTH_SIGNOUT_REDIRECT_URL,
            },
            headers=response.headers,
        )

    return JSONResponse(status_code=200, content={'status': True}, headers=response.headers)


############################
# OAuth Session Management
############################


@router.delete('/oauth/sessions/{provider:path}', response_model=bool)
async def delete_oauth_session_by_provider(
    request: Request,
    provider: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Disconnect the current user's OAuth session for a specific provider.
    The provider string matches the 'provider' field in the oauth_session table
    (e.g. 'mcp:server-id' for MCP connections).
    """
    result = await OAuthSessions.delete_sessions_by_user_id_and_provider(user.id, provider, db=db)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No OAuth session found for this provider',
        )
    await publish_event(
        request,
        EVENTS.AUTH_OAUTH_SESSION_DELETED,
        actor=user,
        subject_id=user.id,
        subject_type='user',
        data={'provider': provider},
    )
    return True


############################
# AddUser
############################


@router.post('/add', response_model=SigninResponse)
async def add_user(
    request: Request,
    form_data: AddUserForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    admin_user = user
    if not validate_email_format(form_data.email.lower()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.INVALID_EMAIL_FORMAT)

    if await Users.get_user_by_email(form_data.email.lower(), db=db):
        raise HTTPException(400, detail=ERROR_MESSAGES.EMAIL_TAKEN)

    try:
        try:
            validate_password(form_data.password)
        except Exception as e:
            raise HTTPException(400, detail=str(e))

        hashed = await get_password_hash(form_data.password)
        user = await Auths.insert_new_auth(
            form_data.email.lower(),
            hashed,
            form_data.name,
            form_data.profile_image_url,
            form_data.role,
            db=db,
        )

        if user:
            await apply_default_group_assignment(
                request.app.state.config.DEFAULT_GROUP_ID,
                user.id,
                db=db,
            )
            await publish_event(
                request,
                EVENTS.USER_CREATED,
                actor=admin_user,
                subject_id=user.id,
                source='admin',
                data={'role': user.role},
            )

            expires_delta = parse_duration(request.app.state.config.JWT_EXPIRES_IN)
            token = create_token(data={'id': user.id}, expires_delta=expires_delta)
            return {
                'token': token,
                'token_type': 'Bearer',
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'role': user.role,
                'profile_image_url': f'/api/v1/users/{user.id}/profile/image',
            }
        else:
            raise HTTPException(500, detail=ERROR_MESSAGES.CREATE_USER_ERROR)
    except HTTPException:
        raise
    except Exception as err:
        log.error(f'Add user error: {str(err)}')
        raise HTTPException(500, detail='An internal error occurred while adding the user.')


############################
# GetAdminDetails
############################


@router.get('/admin/details')
async def get_admin_details(
    request: Request, user=Depends(get_current_user), db: AsyncSession = Depends(get_async_session)
):
    if request.app.state.config.SHOW_ADMIN_DETAILS:
        admin_email = request.app.state.config.ADMIN_EMAIL
        admin_name = None

        log.info(f'Admin details - Email: {admin_email}, Name: {admin_name}')

        if admin_email:
            admin = await Users.get_user_by_email(admin_email, db=db)
            if admin:
                admin_name = admin.name
        else:
            admin = await Users.get_first_user(db=db)
            if admin:
                admin_email = admin.email
                admin_name = admin.name

        return {
            'name': admin_name,
            'email': admin_email,
        }
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.ACTION_PROHIBITED)


############################
# ToggleSignUp
############################


@router.get('/admin/config')
async def get_admin_config(request: Request, user=Depends(get_admin_user)):
    return {
        'SHOW_ADMIN_DETAILS': request.app.state.config.SHOW_ADMIN_DETAILS,
        'ADMIN_EMAIL': request.app.state.config.ADMIN_EMAIL,
        'WEBUI_URL': request.app.state.WEBUI_URL,
        'WEBUI_URL_ENV_CONTROLLED': True,
        'ENABLE_SIGNUP': request.app.state.config.ENABLE_SIGNUP,
        'ENABLE_API_KEYS': request.app.state.config.ENABLE_API_KEYS,
        'ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS': request.app.state.config.ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS,
        'API_KEYS_ALLOWED_ENDPOINTS': request.app.state.config.API_KEYS_ALLOWED_ENDPOINTS,
        'DEFAULT_USER_ROLE': request.app.state.config.DEFAULT_USER_ROLE,
        'DEFAULT_GROUP_ID': request.app.state.config.DEFAULT_GROUP_ID,
        'JWT_EXPIRES_IN': request.app.state.config.JWT_EXPIRES_IN,
        'ENABLE_COMMUNITY_SHARING': request.app.state.config.ENABLE_COMMUNITY_SHARING,
        'ENABLE_MESSAGE_RATING': request.app.state.config.ENABLE_MESSAGE_RATING,
        'ENABLE_FOLDERS': request.app.state.config.ENABLE_FOLDERS,
        'FOLDER_MAX_FILE_COUNT': request.app.state.config.FOLDER_MAX_FILE_COUNT,
        'AUTOMATION_MAX_COUNT': request.app.state.config.AUTOMATION_MAX_COUNT,
        'AUTOMATION_MIN_INTERVAL': request.app.state.config.AUTOMATION_MIN_INTERVAL,
        'ENABLE_AUTOMATIONS': request.app.state.config.ENABLE_AUTOMATIONS,
        'ENABLE_CHANNELS': request.app.state.config.ENABLE_CHANNELS,
        'CHANNEL_MODEL_RESPONSE_MODE': request.app.state.config.CHANNEL_MODEL_RESPONSE_MODE,
        'ENABLE_CALENDAR': request.app.state.config.ENABLE_CALENDAR,
        'ENABLE_MEMORIES': request.app.state.config.ENABLE_MEMORIES,
        'ENABLE_MEMORY_SYSTEM_CONTEXT': request.app.state.config.ENABLE_MEMORY_SYSTEM_CONTEXT,
        'ENABLE_NOTES': request.app.state.config.ENABLE_NOTES,
        'ENABLE_USER_WEBHOOKS': request.app.state.config.ENABLE_USER_WEBHOOKS,
        'ENABLE_USER_STATUS': request.app.state.config.ENABLE_USER_STATUS,
        'PENDING_USER_OVERLAY_TITLE': request.app.state.config.PENDING_USER_OVERLAY_TITLE,
        'PENDING_USER_OVERLAY_CONTENT': request.app.state.config.PENDING_USER_OVERLAY_CONTENT,
        'RESPONSE_WATERMARK': request.app.state.config.RESPONSE_WATERMARK,
    }


class AdminConfig(BaseModel):
    SHOW_ADMIN_DETAILS: bool
    ADMIN_EMAIL: str | None = None
    WEBUI_URL: str
    ENABLE_SIGNUP: bool
    ENABLE_API_KEYS: bool
    ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS: bool
    API_KEYS_ALLOWED_ENDPOINTS: str
    DEFAULT_USER_ROLE: str
    DEFAULT_GROUP_ID: str
    JWT_EXPIRES_IN: str
    ENABLE_COMMUNITY_SHARING: bool
    ENABLE_MESSAGE_RATING: bool
    ENABLE_FOLDERS: bool
    FOLDER_MAX_FILE_COUNT: int | str | None = None
    AUTOMATION_MAX_COUNT: int | str | None = None
    AUTOMATION_MIN_INTERVAL: int | str | None = None
    ENABLE_AUTOMATIONS: bool
    ENABLE_CHANNELS: bool
    CHANNEL_MODEL_RESPONSE_MODE: str = 'thread'
    ENABLE_CALENDAR: bool
    ENABLE_MEMORIES: bool
    ENABLE_MEMORY_SYSTEM_CONTEXT: bool
    ENABLE_NOTES: bool
    ENABLE_USER_WEBHOOKS: bool
    ENABLE_USER_STATUS: bool
    PENDING_USER_OVERLAY_TITLE: str | None = None
    PENDING_USER_OVERLAY_CONTENT: str | None = None
    RESPONSE_WATERMARK: str | None = None


@router.post('/admin/config')
async def update_admin_config(request: Request, form_data: AdminConfig, user=Depends(get_admin_user)):
    # Risk #1: this fork keeps the AppConfig backbone, so admin settings are
    # written field-by-field onto request.app.state.config (which proxies the
    # module ConfigVars + persists via .commit()), NOT through upstream's
    # per-key Config.upsert(). WEBUI_URL is deliberately NOT written here — it
    # is controlled exclusively by the WEBUI_URL environment variable
    # (WEBUI_URL_ENV_CONTROLLED), guarded by test_webui_url_config.py.
    config = request.app.state.config

    config.SHOW_ADMIN_DETAILS = form_data.SHOW_ADMIN_DETAILS
    config.ADMIN_EMAIL = form_data.ADMIN_EMAIL
    config.ENABLE_SIGNUP = form_data.ENABLE_SIGNUP

    config.ENABLE_API_KEYS = form_data.ENABLE_API_KEYS
    config.ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS = form_data.ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS
    config.API_KEYS_ALLOWED_ENDPOINTS = form_data.API_KEYS_ALLOWED_ENDPOINTS

    config.ENABLE_FOLDERS = form_data.ENABLE_FOLDERS
    config.FOLDER_MAX_FILE_COUNT = (
        int(form_data.FOLDER_MAX_FILE_COUNT) if form_data.FOLDER_MAX_FILE_COUNT else ''
    )
    config.AUTOMATION_MAX_COUNT = (
        int(form_data.AUTOMATION_MAX_COUNT) if form_data.AUTOMATION_MAX_COUNT else ''
    )
    config.AUTOMATION_MIN_INTERVAL = (
        int(form_data.AUTOMATION_MIN_INTERVAL) if form_data.AUTOMATION_MIN_INTERVAL else ''
    )
    config.ENABLE_AUTOMATIONS = form_data.ENABLE_AUTOMATIONS
    config.ENABLE_CHANNELS = form_data.ENABLE_CHANNELS
    if form_data.CHANNEL_MODEL_RESPONSE_MODE in ['thread', 'channel']:
        config.CHANNEL_MODEL_RESPONSE_MODE = form_data.CHANNEL_MODEL_RESPONSE_MODE
    config.ENABLE_CALENDAR = form_data.ENABLE_CALENDAR
    config.ENABLE_MEMORIES = form_data.ENABLE_MEMORIES
    config.ENABLE_MEMORY_SYSTEM_CONTEXT = form_data.ENABLE_MEMORY_SYSTEM_CONTEXT
    config.ENABLE_NOTES = form_data.ENABLE_NOTES

    if form_data.DEFAULT_USER_ROLE in ['pending', 'user', 'admin']:
        config.DEFAULT_USER_ROLE = form_data.DEFAULT_USER_ROLE

    config.DEFAULT_GROUP_ID = form_data.DEFAULT_GROUP_ID

    pattern = r'^(-1|0|(-?\d+(\.\d+)?)(ms|s|m|h|d|w))$'
    if re.match(pattern, form_data.JWT_EXPIRES_IN):
        config.JWT_EXPIRES_IN = form_data.JWT_EXPIRES_IN

    config.ENABLE_COMMUNITY_SHARING = form_data.ENABLE_COMMUNITY_SHARING
    config.ENABLE_MESSAGE_RATING = form_data.ENABLE_MESSAGE_RATING

    config.ENABLE_USER_WEBHOOKS = form_data.ENABLE_USER_WEBHOOKS
    config.ENABLE_USER_STATUS = form_data.ENABLE_USER_STATUS

    config.PENDING_USER_OVERLAY_TITLE = form_data.PENDING_USER_OVERLAY_TITLE
    config.PENDING_USER_OVERLAY_CONTENT = form_data.PENDING_USER_OVERLAY_CONTENT

    config.RESPONSE_WATERMARK = form_data.RESPONSE_WATERMARK

    return await get_config_values(ADMIN_CONFIG_KEYS)


class LdapServerConfig(BaseModel):
    label: str
    host: str
    port: int | None = None
    attribute_for_mail: str = 'mail'
    attribute_for_username: str = 'uid'
    app_dn: str
    app_dn_password: str
    search_base: str
    search_filters: str = ''
    use_tls: bool = True
    certificate_path: str | None = None
    validate_cert: bool = True
    ciphers: str | None = 'ALL'
    enable_group_management: bool = False
    enable_group_creation: bool = False
    attribute_for_groups: str = 'memberOf'


@router.get('/admin/config/ldap/server', response_model=LdapServerConfig)
async def get_ldap_server(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(LDAP_SERVER_CONFIG_KEYS)


@router.post('/admin/config/ldap/server')
async def update_ldap_server(request: Request, form_data: LdapServerConfig, user=Depends(get_admin_user)):
    required_fields = [
        'label',
        'host',
        'attribute_for_mail',
        'attribute_for_username',
        'search_base',
    ]
    for key in required_fields:
        value = getattr(form_data, key)
        if not value:
            raise HTTPException(400, detail=ERROR_MESSAGES.REQUIRED_FIELD_EMPTY(key))

    # The group attribute is what group management reads from the directory
    # entry; an empty value would make group sync silently do nothing.
    if form_data.enable_group_management and not (form_data.attribute_for_groups or '').strip():
        raise HTTPException(400, detail=ERROR_MESSAGES.REQUIRED_FIELD_EMPTY('attribute_for_groups'))

    config_updates(form_data.model_dump(), LDAP_SERVER_CONFIG_KEYS)
    request.app.state.config.LDAP_APP_DN = form_data.app_dn or ''
    request.app.state.config.LDAP_APP_PASSWORD = form_data.app_dn_password or ''
    return await get_config_values(LDAP_SERVER_CONFIG_KEYS)


@router.get('/admin/config/ldap')
async def get_ldap_config(request: Request, user=Depends(get_admin_user)):
    return {'ENABLE_LDAP': request.app.state.config.ENABLE_LDAP}


class LdapConfigForm(BaseModel):
    enable_ldap: bool | None = None


@router.post('/admin/config/ldap')
async def update_ldap_config(request: Request, form_data: LdapConfigForm, user=Depends(get_admin_user)):
    request.app.state.config.ENABLE_LDAP = form_data.enable_ldap
    return {'ENABLE_LDAP': request.app.state.config.ENABLE_LDAP}


############################
# API Key
############################


class OAuthConfigForm(BaseModel):
    """All OAuth/OIDC settings exposed to the admin panel (v0.11.0)."""

    ENABLE_OAUTH: bool | None = None
    ENABLE_OAUTH_SIGNUP: bool | None = None
    OAUTH_MERGE_ACCOUNTS_BY_EMAIL: bool | None = None
    OAUTH_AUTO_REDIRECT: bool | None = None
    OAUTH_ALLOWED_DOMAINS: str | list | None = None
    OAUTH_BLOCKED_GROUPS: str | None = None
    ENABLE_OAUTH_ROLE_MANAGEMENT: bool | None = None
    OAUTH_ROLES_CLAIM: str | None = None
    OAUTH_ADMIN_ROLES: str | list | None = None
    OAUTH_ALLOWED_ROLES: str | list | None = None
    ENABLE_OAUTH_GROUP_MANAGEMENT: bool | None = None
    ENABLE_OAUTH_GROUP_CREATION: bool | None = None
    OAUTH_GROUPS_CLAIM: str | None = None
    OAUTH_GROUP_DEFAULT_SHARE: bool | str | None = None
    OAUTH_PROVIDER_NAME: str | None = None
    OPENID_PROVIDER_URL: str | None = None
    OAUTH_CLIENT_ID: str | None = None
    OAUTH_CLIENT_SECRET: str | None = None
    OPENID_REDIRECT_URI: str | None = None
    OAUTH_SCOPES: str | None = None
    OAUTH_CODE_CHALLENGE_METHOD: str | None = None
    OAUTH_TOKEN_ENDPOINT_AUTH_METHOD: str | None = None
    OPENID_END_SESSION_ENDPOINT: str | None = None
    OAUTH_TIMEOUT: int | str | None = None
    OAUTH_CLIENT_TIMEOUT: int | str | None = None
    OAUTH_EMAIL_CLAIM: str | None = None
    OAUTH_USERNAME_CLAIM: str | None = None
    OAUTH_PICTURE_CLAIM: str | None = None
    OAUTH_SUB_CLAIM: str | None = None
    OAUTH_AUDIENCE: str | None = None
    OAUTH_UPDATE_EMAIL_ON_LOGIN: bool | None = None
    OAUTH_UPDATE_NAME_ON_LOGIN: bool | None = None
    OAUTH_UPDATE_PICTURE_ON_LOGIN: bool | None = None
    OAUTH_REFRESH_TOKEN_INCLUDE_SCOPE: bool | None = None


# Fields the fork stores as comma-strings but the form exposes as lists (or vice versa).
OAUTH_COMMA_LIST_FIELDS = {'OAUTH_ALLOWED_DOMAINS', 'OAUTH_ADMIN_ROLES', 'OAUTH_ALLOWED_ROLES'}

# form-field name → fork ConfigVar attribute name (Risk #1: no dotted keys).
OAUTH_CONFIG_KEYS = {f: f for f in OAuthConfigForm.model_fields}


def _format_oauth_form_value(field: str, value):
    if field in OAUTH_COMMA_LIST_FIELDS and isinstance(value, list):
        return ','.join(str(item) for item in value)
    return value


def _parse_oauth_update_value(field: str, value):
    if field in OAUTH_COMMA_LIST_FIELDS and isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    if field in {'OAUTH_TIMEOUT', 'OAUTH_CLIENT_TIMEOUT'} and value == '':
        return ''
    return value


async def get_oauth_config_values() -> dict:
    from open_webui import config as _cfg

    out = {}
    for field in OAUTH_CONFIG_KEYS:
        cv = getattr(_cfg, field, None)
        out[field] = _format_oauth_form_value(field, getattr(cv, 'value', None))
    return out


def oauth_config_updates(request: Request, data: dict) -> None:
    from open_webui import config as _cfg

    for field, value in data.items():
        if field not in OAUTH_CONFIG_KEYS:
            continue
        cv = getattr(_cfg, field, None)
        if cv is not None:
            setattr(request.app.state.config, field, _parse_oauth_update_value(field, value))


@router.get('/admin/config/oauth', response_model=OAuthConfigForm)
async def get_oauth_config(request: Request, user=Depends(get_admin_user)):
    return await get_oauth_config_values()


@router.post('/admin/config/oauth', response_model=OAuthConfigForm)
async def update_oauth_config(request: Request, form_data: OAuthConfigForm, user=Depends(get_admin_user)):
    oauth_config_updates(request, form_data.model_dump(exclude_none=True))
    return await get_oauth_config_values()


async def _check_api_key_permission(request: Request, user, db: AsyncSession):
    if not request.app.state.config.ENABLE_API_KEYS or (
        user.role != 'admin'
        and not await has_permission(
            user.id, 'features.api_keys', request.app.state.config.USER_PERMISSIONS, db=db
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.API_KEY_CREATION_NOT_ALLOWED,
        )


# create api key
@router.post('/api_key', response_model=ApiKey)
async def generate_api_key(
    request: Request, user=Depends(get_current_user), db: AsyncSession = Depends(get_async_session)
):
    await _check_api_key_permission(request, user, db)

    api_key = create_api_key()
    success = await Users.update_user_api_key_by_id(user.id, api_key, db=db)

    if success:
        await publish_event(
            request,
            EVENTS.AUTH_API_KEY_CREATED,
            actor=user,
            subject_id=user.id,
            subject_type='user',
        )
        return {
            'api_key': api_key,
        }
    else:
        raise HTTPException(500, detail=ERROR_MESSAGES.CREATE_API_KEY_ERROR)


# delete api key
@router.delete('/api_key', response_model=bool)
async def delete_api_key(
    request: Request, user=Depends(get_current_user), db: AsyncSession = Depends(get_async_session)
):
    await _check_api_key_permission(request, user, db)
    success = await Users.delete_user_api_key_by_id(user.id, db=db)
    if success:
        await publish_event(
            request,
            EVENTS.AUTH_API_KEY_DELETED,
            actor=user,
            subject_id=user.id,
            subject_type='user',
        )
    return success


# get api key
@router.get('/api_key', response_model=ApiKey)
async def get_api_key(request: Request, user=Depends(get_current_user), db: AsyncSession = Depends(get_async_session)):
    await _check_api_key_permission(request, user, db)
    api_key = await Users.get_user_api_key_by_id(user.id, db=db)
    if api_key:
        return {
            'api_key': api_key,
        }
    else:
        raise HTTPException(404, detail=ERROR_MESSAGES.API_KEY_NOT_FOUND)


############################
# Token Exchange
############################


class TokenExchangeForm(BaseModel):
    token: str  # OAuth access token from external provider


async def get_token_client_id(client, token: str) -> str | None:
    """Return the OAuth client_id a token was minted for, when the provider supports introspection."""
    try:
        metadata = await client.load_server_metadata()
        introspection_endpoint = metadata.get('introspection_endpoint')
        if not introspection_endpoint:
            log.warning('Token exchange trusted-client check requires an introspection_endpoint')
            return None

        async with ClientSession(trust_env=True) as session:
            async with session.post(
                introspection_endpoint,
                data={'token': token, 'token_type_hint': 'access_token'},
                auth=BasicAuth(client.client_id, client.client_secret or ''),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                if r.status != 200:
                    log.warning(f'Token introspection returned {r.status}')
                    return None
                introspection = await r.json()

        if not introspection.get('active'):
            log.warning('Token introspection reports the token is inactive')
            return None

        return introspection.get('client_id')
    except Exception as e:
        log.warning(f'Token introspection failed: {e}')
        return None


@router.post('/oauth/{provider}/token/exchange', response_model=SessionUserResponse)
async def token_exchange(
    request: Request,
    response: Response,
    provider: str,
    form_data: TokenExchangeForm,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Exchange an external OAuth provider token for an OpenWebUI JWT.
    This endpoint is disabled by default. Set ENABLE_OAUTH_TOKEN_EXCHANGE=True to enable.
    """
    if not ENABLE_OAUTH_TOKEN_EXCHANGE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Token exchange is disabled',
        )

    if token_exchange_rate_limiter and token_exchange_rate_limiter.is_limited(
        request.client.host if request.client else 'unknown'
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=ERROR_MESSAGES.RATE_LIMIT_EXCEEDED,
        )

    provider = provider.lower()

    # Check if provider is configured
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.OAUTH_NOT_CONFIGURED(provider),
        )
    # Get the OAuth client for this provider
    oauth_manager = request.app.state.oauth_manager
    client = oauth_manager.get_client(provider)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.OAUTH_NOT_CONFIGURED(provider),
        )

    if OAUTH_TOKEN_EXCHANGE_TRUSTED_CLIENT_IDS:
        token_client_id = await get_token_client_id(client, form_data.token)
        if not token_client_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Unable to determine which client the token was issued to',
            )
        if token_client_id not in OAUTH_TOKEN_EXCHANGE_TRUSTED_CLIENT_IDS:
            log.warning('Token exchange denied: token was issued to an untrusted client for %s', provider)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

    # Validate the token by calling the userinfo endpoint
    try:
        token_data = {'access_token': form_data.token, 'token_type': 'Bearer'}
        user_data = await client.userinfo(token=token_data)

        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid token or unable to fetch user info',
            )
    except Exception as e:
        log.warning(f'Token exchange failed for provider {provider}: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid token or unable to validate with provider',
        )

    # Extract user information from the token claims
    email_claim = request.app.state.config.OAUTH_EMAIL_CLAIM

    # Get sub claim
    sub_claim = request.app.state.config.OAUTH_SUB_CLAIM
    sub = user_data.get(sub_claim or OAUTH_PROVIDERS[provider].get('sub_claim', 'sub'))
    if not sub:
        log.warning(f'Token exchange failed: sub claim missing from user data')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token missing required 'sub' claim",
        )

    email = user_data.get(email_claim, '')
    if not email:
        log.warning(f'Token exchange failed: email claim missing from user data')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Token missing required email claim',
        )
    email = email.lower()

    # Enforce domain allowlist — same check as the normal OAuth callback
    if (
        '*' not in auth_manager_config.OAUTH_ALLOWED_DOMAINS
        and email.split('@')[-1] not in auth_manager_config.OAUTH_ALLOWED_DOMAINS
    ):
        log.warning(f'Token exchange denied: email domain not in allowed domains list')
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    # Try to find the user by OAuth sub
    user = await Users.get_user_by_oauth_sub(provider, sub, db=db)

    if not user and request.app.state.config.OAUTH_MERGE_ACCOUNTS_BY_EMAIL:
        # Try to find by email if merge is enabled
        user = await Users.get_user_by_email(email, db=db)
        if user:
            # Link the OAuth sub to this user
            await Users.update_user_oauth_by_id(user.id, provider, sub, db=db)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='User not found. Please sign in via the web interface first.',
        )

    return await create_session_response(request, user, db, source='oauth')
