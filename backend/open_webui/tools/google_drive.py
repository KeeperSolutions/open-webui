"""
Built-in Google Drive tools, exposed once the user connects Google Drive (see get_builtin_tools).
"""

import asyncio
import html
import json
import logging
import mimetypes
from urllib.parse import quote

import httpx
from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader

from open_webui.tools.documents import (
    DOCUMENT_MAX_CONTENT_CHARS,
    DOCUMENT_MIME_TYPES,
    _batch_status,
    _build_document_bytes,
    _document_build_failure_message,
    _document_display_name,
    _documents_result,
    _emit_document_cards,
    load_stored_documents,
)

log = logging.getLogger(__name__)


GOOGLE_DRIVE_FILES_URL = 'https://www.googleapis.com/drive/v3/files'
GOOGLE_DRIVE_UPLOAD_URL = 'https://www.googleapis.com/upload/drive/v3/files'

GOOGLE_DRIVE_NATIVE_EXPORT_MIME_TYPES = {
    'application/vnd.google-apps.document': 'text/html',
    'application/vnd.google-apps.spreadsheet': 'text/csv',
    'application/vnd.google-apps.presentation': 'text/plain',
}

# Docs export as HTML above so headings/bold/lists survive - converted to markdown in drive_read
GOOGLE_DRIVE_HTML_EXPORT_MIME_TYPES = {'application/vnd.google-apps.document'}


def _normalize_google_docs_html(html: str) -> str:
    """Google Docs' HTML export marks bold/italic/headings via CSS classes (e.g. font-weight:700
    on a <span class="c1">) rather than semantic tags, so markdownify can't see them - this
    rewrites those spans/paragraphs into <strong>/<em>/<hN> first."""
    import re

    from bs4 import BeautifulSoup

    style_rules: dict[str, dict[str, str]] = {}
    for class_name, body in re.findall(r'\.([\w-]+)\s*\{([^}]*)\}', html):
        props = {}
        for decl in body.split(';'):
            if ':' in decl:
                key, value = decl.split(':', 1)
                props[key.strip()] = value.strip()
        style_rules[class_name] = props

    soup = BeautifulSoup(html, 'html.parser')

    for el in soup.find_all(['p', 'span']):
        props = {}
        for class_name in el.get('class') or []:
            props.update(style_rules.get(class_name, {}))
        if not props:
            continue

        font_weight = props.get('font-weight', '')
        is_bold = font_weight == 'bold' or (font_weight.isdigit() and int(font_weight) >= 600)
        is_italic = props.get('font-style') == 'italic'

        size_match = re.match(r'([\d.]+)pt', props.get('font-size', ''))
        font_size = float(size_match.group(1)) if size_match else None

        if el.name == 'p' and font_size and font_size >= 13:
            el.name = 'h1' if font_size >= 18 else 'h2' if font_size >= 15 else 'h3'
            continue

        if is_italic:
            el.wrap(soup.new_tag('em'))
        if is_bold:
            el.wrap(soup.new_tag('strong'))

    return str(soup)


DRIVE_MAX_RESPONSE_BYTES = 100_000

# Raw file download cap for drive_read - guards memory use before DRIVE_MAX_RESPONSE_BYTES
# truncation runs, since that truncation only applies after the whole file is downloaded
DRIVE_MAX_DOWNLOAD_BYTES = 25_000_000

# Google Drive quota errors surface as 429, but can also come back as 403 with one of these reasons
DRIVE_RATE_LIMIT_REASONS = {'userRateLimitExceeded', 'rateLimitExceeded', 'quotaExceeded'}


def _drive_error_reason(response) -> str:
    try:
        return response.json().get('error', {}).get('errors', [{}])[0].get('reason', '')
    except Exception:
        return ''


def _drive_is_rate_limited(response) -> bool:
    if response.status_code == 429:
        return True
    return response.status_code == 403 and _drive_error_reason(response) in DRIVE_RATE_LIMIT_REASONS


def _drive_retry_delay_seconds(response) -> float:
    retry_after = response.headers.get('Retry-After')
    if retry_after:
        try:
            return min(float(retry_after), 5.0)
        except ValueError:
            pass
    return 1.5


async def _drive_get(client, url: str, headers: dict, params: dict):
    """GET against the Drive API, retrying once if the first attempt is rate-limited."""
    response = await client.get(url, headers=headers, params=params)
    if _drive_is_rate_limited(response):
        await asyncio.sleep(_drive_retry_delay_seconds(response))
        response = await client.get(url, headers=headers, params=params)
    return response


def _drive_escape_query_value(value: str) -> str:
    """Escape a string for safe use inside a single-quoted Drive API query value."""
    return value.replace('\\', '\\\\').replace("'", "\\'")


async def _drive_get_access_token(__user__: dict) -> str:
    from open_webui.routers.connectors import get_valid_access_token

    user_id = (__user__ or {}).get('id')
    return await get_valid_access_token(user_id) if user_id else None


async def _drive_prepare_write(__user__: dict, __event_call__: callable):
    """Shared preamble for the Drive write tools. Returns (headers, None) on success, or
    (None, error_json) if the caller should return that error immediately."""
    access_token = await _drive_get_access_token(__user__)
    if not access_token:
        return None, json.dumps({'error': "Google Drive isn't connected for this user."})
    if not __event_call__:
        return None, json.dumps({'error': 'Confirmation channel not available.'})
    return {'Authorization': f'Bearer {access_token}'}, None


async def _drive_name_exists(client, headers: dict, name: str, parent_id: str = None) -> bool:
    """Check if a non-trashed file with this exact name already exists in the user's Drive
    (optionally scoped to one folder, since the same name in a different folder isn't a clash)."""
    escaped_name = _drive_escape_query_value(name)
    query = f"name = '{escaped_name}' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    response = await _drive_get(
        client,
        GOOGLE_DRIVE_FILES_URL,
        headers=headers,
        params={
            'q': query,
            'includeItemsFromAllDrives': 'true',
            'supportsAllDrives': 'true',
            'fields': 'files(id)',
            'pageSize': 1,
        },
    )
    return response.status_code == 200 and bool(response.json().get('files'))


DRIVE_ROOT_FOLDER_ALIASES = {'my drive', 'root', 'drive', 'top level', 'the root'}


async def _drive_resolve_folder_id(client, headers: dict, folder_name: str):
    """Look up a folder by exact name. Returns (folder_id, None) on a single match,
    or (None, error_message) if there's no match or more than one.

    "My Drive" (the root) isn't a real folder object in the Drive API, so it can't be found by
    name - it's handled separately here via Drive's special 'root' alias id."""
    if folder_name.strip().lower() in DRIVE_ROOT_FOLDER_ALIASES:
        return 'root', None

    escaped_name = _drive_escape_query_value(folder_name)
    response = await _drive_get(
        client,
        GOOGLE_DRIVE_FILES_URL,
        headers=headers,
        params={
            'q': f"name = '{escaped_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            'includeItemsFromAllDrives': 'true',
            'supportsAllDrives': 'true',
            'fields': 'files(id,name)',
            'pageSize': 10,
        },
    )
    if response.status_code != 200:
        return None, _drive_error_message(response, f'Failed to look up the folder "{folder_name}".')

    folders = response.json().get('files', [])
    if not folders:
        return None, f'No folder named "{folder_name}" was found in your Drive.'
    if len(folders) > 1:
        return None, f'There are {len(folders)} folders named "{folder_name}" - ask the user which one they mean.'

    return folders[0]['id'], None


async def _drive_request(client, method: str, url: str, headers: dict, **kwargs):
    """Make a Drive API request, retrying once if the first attempt is rate-limited."""
    response = await client.request(method, url, headers=headers, **kwargs)
    if _drive_is_rate_limited(response):
        await asyncio.sleep(_drive_retry_delay_seconds(response))
        response = await client.request(method, url, headers=headers, **kwargs)
    return response


# Native Google format each document type converts into on upload - PDF has no native equivalent.
DRIVE_DOCUMENT_NATIVE_MIME_TYPES = {
    'docx': 'application/vnd.google-apps.document',
    'xlsx': 'application/vnd.google-apps.spreadsheet',
    'pptx': 'application/vnd.google-apps.presentation',
}

# Drive takes files up to this size in one multipart request, bigger ones need a resumable upload
DRIVE_MULTIPART_MAX_BYTES = 5_000_000

DRIVE_NATIVE_MIME_TYPE_FORMATS = {v: k for k, v in DRIVE_DOCUMENT_NATIVE_MIME_TYPES.items()}
DRIVE_NATIVE_MIME_TYPE_FORMATS['application/pdf'] = 'pdf'
# Cards also know plain text, which the table above leaves out on purpose so an edited copy of it stays plain text
DRIVE_CARD_FORMATS = {**DRIVE_NATIVE_MIME_TYPE_FORMATS, 'text/plain': 'txt'}


async def _drive_emit_document_card(event_emitter, f: dict, format: str = None, update_only: bool = False):
    """Show a written Drive file as a document card, the same card create_documents uses - always call this on
    success so the user gets a reliable link/download card instead of the model composing its own text link.
    With update_only it only refreshes a card the chat already shows for this file (e.g. after a rename)."""
    format = format or DRIVE_CARD_FORMATS.get(f.get('mimeType', ''))
    name = f['name']
    # Cards show the format on their own, so an uploaded "report.pdf" reads as "report"
    if format and name.lower().endswith(f'.{format}'):
        name = name[: -len(format) - 1]

    await _emit_document_cards(
        event_emitter,
        [{'drive_id': f['id'], 'name': name, 'format': format, 'web_link': f.get('webViewLink')}],
        update_only=update_only,
    )


async def _drive_upload_multipart(
    client, headers: dict, name: str, target_mime_type: str, content_bytes: bytes, source_mime_type: str, parent_id: str = None
):
    """Upload in one request via multipart/related, converting to target_mime_type if it differs from source_mime_type."""
    boundary = 'hubgate_drive_upload_boundary'
    file_metadata = {'name': name, 'mimeType': target_mime_type}
    if parent_id:
        file_metadata['parents'] = [parent_id]
    metadata = json.dumps(file_metadata)
    body = (
        f'--{boundary}\r\n'
        'Content-Type: application/json; charset=UTF-8\r\n\r\n'
        f'{metadata}\r\n'
        f'--{boundary}\r\n'
        f'Content-Type: {source_mime_type}\r\n\r\n'
    ).encode('utf-8') + content_bytes + f'\r\n--{boundary}--'.encode('utf-8')

    upload_headers = {**headers, 'Content-Type': f'multipart/related; boundary={boundary}'}
    return await _drive_request(
        client,
        'POST',
        GOOGLE_DRIVE_UPLOAD_URL,
        headers=upload_headers,
        params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id,name,mimeType,webViewLink'},
        content=body,
    )


async def _drive_upload_resumable(
    client, headers: dict, name: str, target_mime_type: str, content_bytes: bytes, source_mime_type: str, parent_id: str = None
):
    """Upload in two requests (open a session, then send the bytes) for files too big for multipart."""
    file_metadata = {'name': name, 'mimeType': target_mime_type}
    if parent_id:
        file_metadata['parents'] = [parent_id]

    session = await _drive_request(
        client,
        'POST',
        GOOGLE_DRIVE_UPLOAD_URL,
        headers={
            **headers,
            'Content-Type': 'application/json; charset=UTF-8',
            'X-Upload-Content-Type': source_mime_type,
            'X-Upload-Content-Length': str(len(content_bytes)),
        },
        params={'uploadType': 'resumable', 'supportsAllDrives': 'true', 'fields': 'id,name,mimeType,webViewLink'},
        content=json.dumps(file_metadata),
    )
    session_url = session.headers.get('Location')
    if session.status_code != 200 or not session_url:
        return session

    return await _drive_request(
        client, 'PUT', session_url, headers={**headers, 'Content-Type': source_mime_type}, content=content_bytes
    )


async def _drive_upload_document_bytes(
    client, headers: dict, name: str, format: str, file_bytes: bytes, parent_id: str = None
):
    source_mime_type = DOCUMENT_MIME_TYPES[format]
    target_mime_type = DRIVE_DOCUMENT_NATIVE_MIME_TYPES.get(format, source_mime_type)
    upload = _drive_upload_resumable if len(file_bytes) > DRIVE_MULTIPART_MAX_BYTES else _drive_upload_multipart
    return await upload(
        client, headers, name, target_mime_type, file_bytes, source_mime_type, parent_id=parent_id
    )


async def _drive_create_plain_file(
    client, headers: dict, name: str, content: str, mime_type: str = 'text/plain', parent_id: str = None
):
    """Create a plain file and, if content is given, upload it as the file's body.
    Returns (file_dict, None) on success or (None, error_message) on failure."""
    file_metadata = {'name': name, 'mimeType': mime_type}
    if parent_id:
        file_metadata['parents'] = [parent_id]

    f, error = await _drive_call_raw(
        client,
        'POST',
        GOOGLE_DRIVE_FILES_URL,
        headers,
        'Google Drive file creation failed',
        'Failed to create this file.',
        params={'supportsAllDrives': 'true', 'fields': 'id,name,mimeType,webViewLink'},
        json=file_metadata,
    )
    if error:
        return None, error

    if content:
        _, error = await _drive_call_raw(
            client,
            'PATCH',
            f'{GOOGLE_DRIVE_UPLOAD_URL}/{f["id"]}',
            {**headers, 'Content-Type': mime_type},
            'Google Drive content upload failed',
            'File was created but content upload failed.',
            params={'uploadType': 'media', 'supportsAllDrives': 'true'},
            content=content.encode('utf-8'),
        )
        if error:
            # Content upload failed after the (empty) file was already created - remove it rather
            # than leaving an orphan behind, since the caller has no id to clean it up with itself
            delete_response = await _drive_request(
                client, 'DELETE', f'{GOOGLE_DRIVE_FILES_URL}/{f["id"]}', headers, params={'supportsAllDrives': 'true'}
            )
            if delete_response.status_code not in (200, 204, 404):
                log.error(
                    f'Failed to clean up orphaned Drive file {f["id"]}: '
                    f'{delete_response.status_code} {delete_response.text}'
                )
            return None, error

    return f, None


def _drive_error_message(response, fallback: str) -> str:
    if _drive_is_rate_limited(response):
        return 'Google Drive is rate-limited right now - try again in a moment.'
    if response.status_code == 401:
        return 'Google Drive access was revoked - please reconnect it in Settings.'
    if response.status_code == 403:
        return "You don't have permission to access this file or Drive."
    if response.status_code == 404:
        return 'This file no longer exists or was deleted.'
    return fallback


async def _drive_call_raw(client, method: str, url: str, headers: dict, log_label: str, fallback: str, **kwargs):
    """Make a Drive API request and check its status. Returns (data, None) on a 200 response, or
    (None, error_message) with the failure already logged - error_message is a plain string, for
    batch call sites that collect it into a `failed` list rather than returning it directly."""
    response = await _drive_request(client, method, url, headers, **kwargs)
    if response.status_code != 200:
        log.error(f'{log_label}: {response.status_code} {response.text}')
        return None, _drive_error_message(response, fallback)
    return response.json(), None


async def _drive_call(client, method: str, url: str, headers: dict, log_label: str, fallback: str, **kwargs):
    """Like _drive_call_raw, but wraps the error as a JSON string ready to return directly from a
    single-file tool (e.g. `if error: return error`)."""
    data, error = await _drive_call_raw(client, method, url, headers, log_label, fallback, **kwargs)
    if error:
        return None, json.dumps({'error': error})
    return data, None


# Shown to the model after a write that shows a Drive document card, so it doesn't repeat the link
DRIVE_CARD_NOTE = 'A card with an Open in Drive link is already shown to the user - do not include a link or the word "here" in your reply.'


def _drive_cancelled(action: str, consequence: str) -> str:
    """Build the JSON response for a write tool the user declined via the confirmation popup."""
    return json.dumps(
        {
            'status': 'cancelled',
            'message': f'The user declined to {action} - this is not an error, they simply chose not to proceed. {consequence} because they cancelled it, and do not retry.',
        }
    )


async def _drive_fetch_metadata(client, headers: dict, file_id: str, fields: str, action_desc: str):
    """GET a file's metadata fields. Returns (data, None) on success or (None, error_message)."""
    response = await _drive_get(
        client,
        f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}',
        headers=headers,
        params={'fields': fields, 'supportsAllDrives': 'true'},
    )
    if response.status_code != 200:
        log.error(f'Google Drive metadata fetch failed: {response.status_code} {response.text}')
        return None, _drive_error_message(response, f'Failed to {action_desc}.')
    return response.json(), None


async def _drive_resolve_optional_folder(headers: dict, folder: str, default_id: str = None, client=None):
    """Resolve an optional folder name to an id. Returns (folder_id, None), or (default_id, None)
    without a network call if folder is empty, or (None, error_message) if it can't be resolved.
    Reuses the caller's client if one is passed, otherwise opens its own."""
    if not folder:
        return default_id, None

    try:
        if client is not None:
            return await _drive_resolve_folder_id(client, headers, folder)
        async with httpx.AsyncClient() as new_client:
            return await _drive_resolve_folder_id(new_client, headers, folder)
    except Exception as e:
        log.exception(f'drive folder lookup error: {e}')
        return None, str(e)


def _drive_files_page_result(data: dict, tool_name: str, extra_fields: callable = None) -> dict:
    """Shape a Drive files.list response into the {results, next_page_token} shape our
    search/listing tools return, tagging each file with any tool-specific extra fields."""
    result = {
        'results': [
            {
                'id': f['id'],
                'name': f['name'],
                'mime_type': f['mimeType'],
                'modified_time': f.get('modifiedTime'),
                'web_link': f.get('webViewLink'),
                **(extra_fields(f) if extra_fields else {}),
            }
            for f in data.get('files', [])
        ]
    }
    if data.get('nextPageToken'):
        result['next_page_token'] = data['nextPageToken']
        result['note'] = (
            f'{len(result["results"])} result(s) shown; more are available. '
            f"If these don't already answer the user's request, call {tool_name} again "
            'with this page_token - otherwise, answer with what you have.'
        )
    return result


async def drive_search(
    query: str,
    page_token: str = '',
    include_trashed: bool = False,
    __user__: dict = None,
) -> str:
    """
    Search the current user's Google Drive (including shared drives) by file name or content.

    :param query: Search query matched against file name and content
    :param page_token: Pass the next_page_token from a previous result to fetch the next page
    :param include_trashed: Set to true to search files in Trash instead - use this to find the id of a file the user wants restored with drive_restore_files
    :return: JSON with matching files (id, name, mime_type, modified_time, web_link) and an optional next_page_token if more results are available - use the file id with drive_read to fetch a file's contents
    """

    access_token = await _drive_get_access_token(__user__)
    if not access_token:
        return json.dumps({'error': "Google Drive isn't connected for this user."})

    try:
        escaped_query = _drive_escape_query_value(query)
        trashed_clause = 'trashed = true' if include_trashed else 'trashed = false'

        params = {
            'q': f"(fullText contains '{escaped_query}' or name contains '{escaped_query}') and {trashed_clause}",
            'includeItemsFromAllDrives': 'true',
            'supportsAllDrives': 'true',
            'fields': 'nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink)',
            'pageSize': 25,
        }
        if page_token:
            params['pageToken'] = page_token

        async with httpx.AsyncClient() as client:
            response = await _drive_get(
                client,
                GOOGLE_DRIVE_FILES_URL,
                headers={'Authorization': f'Bearer {access_token}'},
                params=params,
            )

        if response.status_code != 200:
            log.error(f'Google Drive search failed: {response.status_code} {response.text}')
            return json.dumps({'error': _drive_error_message(response, 'Failed to search Google Drive.')})

        return json.dumps(_drive_files_page_result(response.json(), 'drive_search'), ensure_ascii=False)
    except Exception as e:
        log.exception(f'drive_search error: {e}')
        return json.dumps({'error': str(e)})


async def drive_list_folder(
    folder: str = '',
    page_token: str = '',
    __user__: dict = None,
) -> str:
    """
    List the files and subfolders directly inside a Google Drive folder - use this to see what's
    in a folder, instead of drive_search, when the user doesn't give you a filename or keyword to
    search for (e.g. "what's in my Reports folder?", or checking what's already in a folder
    before creating something there).

    :param folder: Name of the folder to list. Pass "My Drive" (or leave empty) for the top level
    :param page_token: Pass the next_page_token from a previous result to fetch the next page
    :return: JSON with the folder's direct contents (id, name, mime_type, modified_time,
        web_link, is_folder) and an optional next_page_token if more results are available -
        subfolders aren't expanded, call this again with a subfolder's name to see inside it
    """

    access_token = await _drive_get_access_token(__user__)
    if not access_token:
        return json.dumps({'error': "Google Drive isn't connected for this user."})

    try:
        headers = {'Authorization': f'Bearer {access_token}'}

        async with httpx.AsyncClient() as client:
            folder_id, folder_error = await _drive_resolve_folder_id(client, headers, folder or 'My Drive')
            if folder_error:
                return json.dumps({'error': folder_error})

            params = {
                'q': f"'{folder_id}' in parents and trashed = false",
                'includeItemsFromAllDrives': 'true',
                'supportsAllDrives': 'true',
                'fields': 'nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink)',
                'pageSize': 50,
            }
            if page_token:
                params['pageToken'] = page_token

            response = await _drive_get(client, GOOGLE_DRIVE_FILES_URL, headers=headers, params=params)

        if response.status_code != 200:
            log.error(f'Google Drive folder listing failed: {response.status_code} {response.text}')
            return json.dumps({'error': _drive_error_message(response, 'Failed to list this folder.')})

        result = _drive_files_page_result(
            response.json(),
            'drive_list_folder',
            lambda f: {'is_folder': f['mimeType'] == 'application/vnd.google-apps.folder'},
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'drive_list_folder error: {e}')
        return json.dumps({'error': str(e)})


def _drive_is_text_mime_type(mime_type: str) -> bool:
    return mime_type.startswith('text/') or mime_type in (
        'application/json',
        'application/xml',
        'application/x-yaml',
    )


# Binary formats drive_read can extract readable text from, mapped to the langchain loader class
# (already a dependency - used for the same formats during RAG ingestion) that handles each one
DRIVE_BINARY_TEXT_EXTRACTORS = {
    'application/pdf': PyPDFLoader,
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': Docx2txtLoader,
}


def _drive_extract_binary_text(mime_type: str, content_bytes: bytes) -> str:
    """Write content_bytes to a temp file and run it through the matching loader - these
    langchain loaders need a real file path, not bytes, and are synchronous/CPU-bound so this
    must be called via asyncio.to_thread rather than awaited directly."""
    import os
    import tempfile

    extension = mimetypes.guess_extension(mime_type) or ''
    fd, temp_path = tempfile.mkstemp(suffix=extension)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content_bytes)
        loader = DRIVE_BINARY_TEXT_EXTRACTORS[mime_type](temp_path)
        return '\n\n'.join(doc.page_content for doc in loader.load())
    finally:
        os.remove(temp_path)


async def drive_read(
    file_id: str,
    __user__: dict = None,
) -> str:
    """
    Read the contents of a Google Drive file by id (as returned by drive_search).

    Google Docs are exported as markdown (headings, bold, lists are preserved). Sheets/Slides
    export to CSV/plain text. Other files (e.g. PDF, DOCX) are downloaded directly if the owner
    allows it. Google Vids are not supported yet. Large files are truncated with a note.

    Note: if the source Google Doc has multiple tabs, their content is flattened into one
    continuous document - separate named tabs can't be read or reproduced through this connector.

    This connector can't edit an existing file in place. If the user asks to edit, update, or
    fix something in a file, read it with this tool, preserve its markdown structure (headings,
    bold, lists) while applying the requested changes, then call drive_save_edited_copy with the
    same file_id and your edited content - it automatically matches the original's format, so
    you don't need to figure out which format or which other tool to use. This is a single
    read-then-save flow - don't call drive_copy_files first, that would ask the user to confirm
    twice and leave behind an unedited duplicate.

    :param file_id: The Google Drive file id to read
    :return: The file's text content, or an error message. Don't paste this whole result back
        into your reply to the user - summarize it, answer their question about it, or use it to
        prepare an edit, unless they explicitly asked to see the raw content
    """

    access_token = await _drive_get_access_token(__user__)
    if not access_token:
        return json.dumps({'error': "Google Drive isn't connected for this user."})

    try:
        headers = {'Authorization': f'Bearer {access_token}'}

        async with httpx.AsyncClient() as client:
            metadata, error = await _drive_fetch_metadata(
                client, headers, file_id, 'mimeType,size,capabilities(canDownload)', 'read this file from Google Drive'
            )
            if error:
                return json.dumps({'error': error})

            mime_type = metadata['mimeType']

            # Google-native exports (Docs/Sheets/Slides) are already size-capped by Google's own
            # export endpoint - this only guards the raw-download path below, which would
            # otherwise buffer the whole file into memory before the truncation further down runs
            if (
                not mime_type.startswith('application/vnd.google-apps.')
                and int(metadata.get('size') or 0) > DRIVE_MAX_DOWNLOAD_BYTES
            ):
                return json.dumps({'error': 'This file is too large to read (over 25 MB).'})

            if mime_type in GOOGLE_DRIVE_NATIVE_EXPORT_MIME_TYPES:
                content_response = await _drive_get(
                    client,
                    f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}/export',
                    headers=headers,
                    params={'mimeType': GOOGLE_DRIVE_NATIVE_EXPORT_MIME_TYPES[mime_type]},
                )
            elif mime_type.startswith('application/vnd.google-apps.'):
                return json.dumps({'error': f"This file's format ({mime_type}) can't be read in the first release."})
            else:
                if not metadata.get('capabilities', {}).get('canDownload', True):
                    return json.dumps({'error': 'The owner has restricted downloading of this file.'})

                content_response = await _drive_get(
                    client,
                    f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}',
                    headers=headers,
                    params={'alt': 'media', 'supportsAllDrives': 'true'},
                )

        if content_response.status_code != 200:
            log.error(f'Google Drive content fetch failed: {content_response.status_code} {content_response.text}')
            return json.dumps(
                {'error': _drive_error_message(content_response, 'Failed to read this file from Google Drive.')}
            )

        content_bytes = content_response.content

        if mime_type in GOOGLE_DRIVE_HTML_EXPORT_MIME_TYPES:
            from markdownify import markdownify

            html = _normalize_google_docs_html(content_bytes.decode('utf-8', errors='replace'))
            content_bytes = markdownify(html).encode('utf-8')
        elif mime_type not in GOOGLE_DRIVE_NATIVE_EXPORT_MIME_TYPES and not _drive_is_text_mime_type(mime_type):
            if mime_type not in DRIVE_BINARY_TEXT_EXTRACTORS:
                return json.dumps(
                    {'error': f"This file's format ({mime_type}) can't be read as text - only text, PDF, and DOCX files are supported."}
                )
            try:
                content_bytes = (await asyncio.to_thread(_drive_extract_binary_text, mime_type, content_bytes)).encode(
                    'utf-8'
                )
            except Exception as e:
                log.exception(f'drive_read binary extraction error: {e}')
                return json.dumps({'error': f'Failed to extract text from this {mime_type} file.'})

        truncated = len(content_bytes) > DRIVE_MAX_RESPONSE_BYTES
        content = content_bytes[:DRIVE_MAX_RESPONSE_BYTES].decode('utf-8', errors='replace')
        if truncated:
            content += '\n\n[Content truncated - file exceeds the maximum readable size.]'

        return content
    except Exception as e:
        log.exception(f'drive_read error: {e}')
        return json.dumps({'error': str(e)})


async def drive_save_edited_copy(
    file_id: str,
    content: str,
    name: str = '',
    folder: str = '',
    __user__: dict = None,
    __event_call__: callable = None,
    __event_emitter__: callable = None,
) -> str:
    """
    Save an edited version of an existing Google Drive file as a new file. This is the ONLY
    tool to use for "edit/update/fix this file" requests - not drive_create_files or
    create_documents. The save format (Google Doc, Sheet, Slides, PDF, or plain text) is
    detected automatically from the original file, so you never need to figure out or specify a
    format yourself - just pass the same file_id you read, and your edited content.

    Read the original with drive_read first, apply the requested changes to its content while
    preserving its markdown structure (headings, bold, lists), then call this tool.

    Before calling it, send one short line telling the user what you're about to do (e.g. "Saving
    your edit now.") - the popup this tool shows already handles asking permission, this is just
    so the user sees something instead of a blank reply while it loads.

    :param file_id: The id of the file being edited (from drive_search or drive_read) - the
        SAME id you passed to drive_read, not a new or copied file
    :param content: Your edited content, in the same shape drive_read gave you: markdown for a
        Google Doc/PDF original, CSV for a Sheet, "---"-separated slides for a Slides deck
        (first line of each slide is the title, remaining lines are bullet points), or plain
        text if the original was already plain text
    :param name: Only pass this if the user asked for a specific name for the saved copy. Leave
        it empty otherwise (don't just repeat the original file's name) - it will automatically
        default to "Edit of <original name>"
    :param folder: Only pass this if the user asked to save the edit into a different folder.
        Leave it empty otherwise - it defaults to the same folder the original file is in.
        Pass "My Drive" for the top level (Drive's root)
    :return: JSON with the created file's id and name. There is no link in this result on purpose
        - the UI already shows an "Open in Drive" button for this file, so don't invent or repeat
        a link (or the word "here") in your reply to the user
    """
    import re

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    try:
        async with httpx.AsyncClient() as client:
            original, error = await _drive_fetch_metadata(
                client, headers, file_id, 'name,mimeType,parents', 'save this edit'
            )
            if error:
                return json.dumps({'error': error})

            original_name = original.get('name', 'this file')
            # None means the original wasn't a rich Google-native/PDF type - save as plain text instead
            format = DRIVE_NATIVE_MIME_TYPE_FORMATS.get(original.get('mimeType', ''))

            # Default to the original's own folder (not Drive root) unless a different one was requested
            original_folder_id = (original.get('parents') or [None])[0]
            folder_id, folder_error = await _drive_resolve_optional_folder(
                headers, folder, default_id=original_folder_id, client=client
            )
    except Exception as e:
        log.exception(f'drive_save_edited_copy metadata error: {e}')
        return json.dumps({'error': str(e)})
    if folder_error:
        return json.dumps({'error': folder_error})
    if format and len(content) > DOCUMENT_MAX_CONTENT_CHARS:
        return json.dumps(
            {'error': f'The content is longer than {DOCUMENT_MAX_CONTENT_CHARS:,} characters - shorten the edit.'}
        )

    # Treat a model-echoed "Edit of <original>" guess (any case) as no name given, not a rename
    stripped_name = name.strip()
    match = re.match(r'^edit of\s+(.*)$', stripped_name, re.IGNORECASE)
    if match:
        stripped_name = match.group(1).strip()
    if stripped_name.casefold() == original_name.strip().casefold():
        name = ''

    # Drive allows duplicate names, so this never needs to check for or dodge one - same as
    # drive_copy_files always naming things "Copy of X".
    save_name = name or f'Edit of {original_name}'

    display_name = f'{save_name}.{format}' if format else save_name
    message = f'Save edit as "{html.escape(display_name)}"' + (f' in "{html.escape(folder)}"?' if folder else '?')

    confirmed = await __event_call__(
        {
            'type': 'confirmation',
            'data': {
                'title': 'Save edited file?',
                'message': message,
                'action': 'drive_save_edited_copy',
                'allow_remember': True,
            },
        }
    )
    if confirmed is not True:
        return _drive_cancelled('save this edit', 'Tell the user the edit was not saved')

    if format:
        try:
            file_bytes = await _build_document_bytes(format, save_name, content)
        except Exception as e:
            log.exception(f'drive_save_edited_copy build error: {e}')
            return json.dumps({'error': _document_build_failure_message(format, e)})

    try:
        async with httpx.AsyncClient() as client:
            if format:
                response = await _drive_upload_document_bytes(
                    client, headers, save_name, format, file_bytes, parent_id=folder_id
                )
                if response.status_code != 200:
                    log.error(f'Google Drive document upload failed: {response.status_code} {response.text}')
                    return json.dumps({'error': _drive_error_message(response, 'Failed to save this edit.')})
                f = response.json()
            else:
                f, error = await _drive_create_plain_file(client, headers, save_name, content, parent_id=folder_id)
                if error:
                    return json.dumps({'error': error})

        await _drive_emit_document_card(__event_emitter__, f, format=format)
        return json.dumps(
            {'status': 'success', 'id': f['id'], 'name': f['name'], 'note': DRIVE_CARD_NOTE},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'drive_save_edited_copy error: {e}')
        return json.dumps({'error': str(e)})


async def drive_move_files(
    file_ids: list[str],
    folder: str,
    __user__: dict = None,
    __event_call__: callable = None,
    __event_emitter__: callable = None,
) -> str:
    """
    Move one or more existing Google Drive files into a different folder - the files themselves
    are unchanged, they just relocate. This actually relocates the files (their ids and content
    stay the same); it does NOT create copies. Use drive_copy_files instead if the user wants
    duplicates left in both places.

    Pass every file_id the user wants moved in ONE call, even for a single file - they all move
    to the same target folder, and a single confirmation covers the whole batch.

    If Google Drive returns a permission error for a file (e.g. the user doesn't own or have edit
    access to it), that file is reported as failed in the result while the rest still proceed;
    suggest drive_copy_files into the target folder instead for that one.

    Before calling it, send one short line telling the user what you're about to do (e.g. "Moving
    those files now.") - the popup this tool shows already handles asking permission, this is
    just so the user sees something instead of a blank reply while it loads.

    :param file_ids: One or more Google Drive file ids to move (from drive_search or a previous
        write tool's result)
    :param folder: Name of the Drive folder to move the files into. Pass "My Drive" for the top
        level (Drive's root) - it's a valid target even though it isn't a real named folder
    :return: JSON with which files were moved and which failed, or an error message
    """

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    if not file_ids:
        return json.dumps({'error': 'No files given to move.'})

    try:
        async with httpx.AsyncClient() as client:
            (metadata_by_id, failed), (folder_id, folder_error) = await asyncio.gather(
                _drive_fetch_metadata_batch(client, headers, file_ids, 'name,parents', 'move this file'),
                _drive_resolve_folder_id(client, headers, folder),
            )
    except Exception as e:
        log.exception(f'drive_move_files lookup error: {e}')
        return json.dumps({'error': str(e)})
    if folder_error:
        return json.dumps({'error': folder_error})

    names: dict[str, str] = {}
    parents_by_id: dict[str, list] = {}
    for file_id, metadata in metadata_by_id.items():
        current_parents = metadata.get('parents', [])
        if folder_id in current_parents:
            failed.append({'id': file_id, 'error': f'"{metadata.get("name", file_id)}" is already in "{folder}".'})
            continue
        names[file_id] = metadata.get('name', file_id)
        parents_by_id[file_id] = current_parents

    if not names:
        return json.dumps({'status': 'error', 'moved': [], 'failed': failed}, ensure_ascii=False)

    title = 'Move Google Drive file?' if len(names) == 1 else f'Move {len(names)} files?'
    confirmed = await __event_call__(
        {
            'type': 'confirmation',
            'data': {
                'title': title,
                'message': _drive_batch_message(f'Move {{}} to "{_drive_dialog_text(folder)}"?', names),
                'action': 'drive_move_files',
                'allow_remember': True,
            },
        }
    )
    if confirmed is not True:
        return _drive_cancelled('move these files', 'Tell the user the files were not moved')

    async def move_one(client, file_id):
        return await _drive_call_raw(
            client,
            'PATCH',
            f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}',
            headers,
            'Google Drive move failed',
            'Failed to move this file.',
            params={
                'addParents': folder_id,
                'removeParents': ','.join(parents_by_id[file_id]),
                'supportsAllDrives': 'true',
                'fields': 'id,name,mimeType,webViewLink',
            },
        )

    try:
        async with httpx.AsyncClient() as client:
            moved_files, failed = await _drive_run_batch(
                names, failed, lambda file_id: move_one(client, file_id)
            )
    except Exception as e:
        log.exception(f'drive_move_files error: {e}')
        return json.dumps({'error': str(e)})

    result = {
        'status': _batch_status(moved_files, failed),
        'moved': [{'id': f['id'], 'name': f['name'], 'folder': folder} for f in moved_files],
        'failed': failed,
    }
    return json.dumps(result, ensure_ascii=False)


async def _drive_trash_file(client, headers: dict, file_id: str, trashed: bool, fields: str = 'id,name'):
    """PATCH a single file's trashed state. Returns (data, None) or (None, error_message)."""
    action = 'delete' if trashed else 'restore'
    return await _drive_call_raw(
        client,
        'PATCH',
        f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}',
        headers,
        f'Google Drive {action} failed',
        f'Failed to {action} this file.',
        params={'supportsAllDrives': 'true', 'fields': fields},
        json={'trashed': trashed},
    )


async def _drive_fetch_metadata_batch(client, headers: dict, file_ids: list[str], fields: str, action_desc: str):
    """GET metadata for many files concurrently. Returns (metadata_by_id, failed) where failed is
    a list of {"id", "error"} for any file whose lookup failed."""
    results = await asyncio.gather(
        *(_drive_fetch_metadata(client, headers, file_id, fields, action_desc) for file_id in file_ids)
    )
    metadata_by_id: dict[str, dict] = {}
    failed: list[dict] = []
    for file_id, (metadata, error) in zip(file_ids, results):
        if error:
            failed.append({'id': file_id, 'error': error})
        else:
            metadata_by_id[file_id] = metadata
    return metadata_by_id, failed


def _drive_dialog_text(text: str) -> str:
    """User or Drive text for a confirmation dialog, escaped for its HTML and with braces doubled for a template."""
    return html.escape(text).replace('{', '{{').replace('}', '}}')


def _drive_batch_message(template: str, names: dict, trailing: str = '') -> str:
    """Fill `template`'s single `{}` with either the one quoted name, or the count ("3 files")
    followed by an indented, bulleted line for every name below it - so a multi-item confirmation
    renders as a readable list in the dialog instead of one long comma-separated line. Uses <br>
    line breaks rather than a real markdown list, to sidestep list/paragraph margin inconsistency
    in the dialog's rendering. `trailing` (e.g. a restore-window note) stays inline for one file,
    or becomes its own line after the list for many."""
    if len(names) == 1:
        (only_name,) = names.values()
        message = template.format(f'"{html.escape(only_name)}"')
        return f'{message} {trailing}' if trailing else message

    listed = '<br>'.join(f'&nbsp;&nbsp;• "{html.escape(name)}"' for name in names.values())
    message = f'{template.format(f"{len(names)} files")}<br>{listed}'
    return f'{message}<br>{trailing}' if trailing else message


async def _drive_run_batch(ids_to_names: dict, failed: list[dict], call):
    """Run call(id) for every id in ids_to_names concurrently, merging into (succeeded, failed) -
    failed may already carry earlier lookup failures, which are kept and added to. Collects
    exceptions per item rather than letting one bad request cancel the rest of the batch and
    lose the results of sibling mutations that already succeeded."""
    results = await asyncio.gather(*(call(item_id) for item_id in ids_to_names), return_exceptions=True)
    succeeded = []
    for item_id, result in zip(ids_to_names, results):
        if isinstance(result, BaseException):
            log.exception(f'Drive batch operation failed for {item_id}: {result}')
            failed.append({'id': item_id, 'error': str(result)})
            continue
        data, error = result
        if error:
            failed.append({'id': item_id, 'error': error})
        else:
            succeeded.append(data)
    return succeeded, failed


async def drive_delete_files(
    file_ids: list[str],
    __user__: dict = None,
    __event_call__: callable = None,
) -> str:
    """
    Move one or more existing Google Drive files to Trash. This does NOT permanently delete them
    - the user can still restore them from Google Drive's Trash (Drive keeps trashed files for 30
    days before permanently removing them).

    This also deletes folders - a folder is just another file id as far as this tool is concerned.
    When the user wants everything cleared (e.g. "delete everything we made" or "clean this up"),
    gather every file id AND every folder id into the SAME call - don't split it into a folders
    pass and a files pass, and don't pause to check in with the user in between; one call, one
    confirmation, done.

    Pass every file_id the user wants deleted in ONE call, even for a single file - do not call
    this tool once per file. A single confirmation covers the whole batch.

    Call this tool directly as soon as the user asks to delete a file - do NOT ask the user to
    confirm in a chat message yourself first. This tool automatically shows its own confirmation
    popup (with no "don't ask again" option - it always asks, every time, with no skip), so
    asking in text first is redundant and just makes the user confirm twice. This is different
    from a plain acknowledgment though - still send one short line telling the user what you're
    about to do (e.g. "Deleting those files now.") before calling this tool, so they see
    something instead of a blank reply while the popup loads.

    If Google Drive returns a permission error for a file (e.g. the user doesn't own or have edit
    access to it), that file is reported as failed in the result while the rest still proceed.

    :param file_ids: One or more Google Drive file ids to delete (from drive_search or a previous
        write tool's result)
    :return: JSON with which files were moved to Trash and which failed, or an error message
    """

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    if not file_ids:
        return json.dumps({'error': 'No files given to delete.'})

    try:
        async with httpx.AsyncClient() as client:
            names, failed = await _drive_fetch_metadata_batch(client, headers, file_ids, 'name', 'delete this file')
    except Exception as e:
        log.exception(f'drive_delete_files metadata error: {e}')
        return json.dumps({'error': str(e)})
    names = {file_id: metadata.get('name', file_id) for file_id, metadata in names.items()}

    if not names:
        return json.dumps({'status': 'error', 'deleted': [], 'failed': failed}, ensure_ascii=False)

    title = 'Move to Trash?' if len(names) == 1 else f'Move {len(names)} files to Trash?'
    plural = 'it' if len(names) == 1 else 'them'
    message = _drive_batch_message(
        'Move {} to Trash?', names, f"You can restore {plural} from Google Drive's Trash within 30 days."
    )

    # No 'allow_remember' on purpose - deletes always ask, every time, no skip option
    confirmed = await __event_call__({'type': 'confirmation', 'data': {'title': title, 'message': message}})
    if confirmed is not True:
        return _drive_cancelled('delete these files', 'Tell the user the files were not deleted')

    try:
        async with httpx.AsyncClient() as client:
            deleted_files, failed = await _drive_run_batch(
                names, failed, lambda file_id: _drive_trash_file(client, headers, file_id, trashed=True)
            )
    except Exception as e:
        log.exception(f'drive_delete_files error: {e}')
        return json.dumps({'error': str(e)})

    return json.dumps(
        {
            'status': _batch_status(deleted_files, failed),
            'deleted': [{'id': f['id'], 'name': f['name']} for f in deleted_files],
            'failed': failed,
        },
        ensure_ascii=False,
    )


async def drive_restore_files(
    file_ids: list[str],
    __user__: dict = None,
    __event_call__: callable = None,
    __event_emitter__: callable = None,
) -> str:
    """
    Restore one or more Google Drive files out of Trash, undoing a previous drive_delete_files.
    Each file goes back to wherever it was before it was trashed.

    Pass every file_id the user wants restored in ONE call, even for a single file - a single
    confirmation covers the whole batch.

    Before calling it, send one short line telling the user what you're about to do (e.g.
    "Restoring those files now.") - the popup this tool shows already handles asking permission,
    this is just so the user sees something instead of a blank reply while it loads.

    :param file_ids: One or more Google Drive file ids to restore (from drive_search, which
        includes trashed files, or from a previous drive_delete_files result)
    :return: JSON with which files were restored and which failed, or an error message
    """

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    if not file_ids:
        return json.dumps({'error': 'No files given to restore.'})

    try:
        async with httpx.AsyncClient() as client:
            metadata_by_id, failed = await _drive_fetch_metadata_batch(
                client, headers, file_ids, 'name,trashed', 'restore this file'
            )
    except Exception as e:
        log.exception(f'drive_restore_files metadata error: {e}')
        return json.dumps({'error': str(e)})

    names: dict[str, str] = {}
    for file_id, metadata in metadata_by_id.items():
        if not metadata.get('trashed'):
            failed.append({'id': file_id, 'error': f'"{metadata.get("name", file_id)}" is not in Trash.'})
        else:
            names[file_id] = metadata.get('name', file_id)

    if not names:
        return json.dumps({'status': 'error', 'restored': [], 'failed': failed}, ensure_ascii=False)

    title = 'Restore from Trash?' if len(names) == 1 else f'Restore {len(names)} files from Trash?'
    confirmed = await __event_call__(
        {
            'type': 'confirmation',
            'data': {
                'title': title,
                'message': _drive_batch_message('Restore {} from Trash?', names),
                'action': 'drive_restore_files',
                'allow_remember': True,
            },
        }
    )
    if confirmed is not True:
        return _drive_cancelled('restore these files', 'Tell the user the files were not restored')

    try:
        async with httpx.AsyncClient() as client:
            restored_files, failed = await _drive_run_batch(
                names,
                failed,
                lambda file_id: _drive_trash_file(
                    client, headers, file_id, trashed=False, fields='id,name,mimeType,webViewLink'
                ),
            )
    except Exception as e:
        log.exception(f'drive_restore_files error: {e}')
        return json.dumps({'error': str(e)})

    result = {
        'status': _batch_status(restored_files, failed),
        'restored': [{'id': f['id'], 'name': f['name']} for f in restored_files],
        'failed': failed,
    }
    return json.dumps(result, ensure_ascii=False)


async def drive_rename_file(
    file_id: str,
    name: str,
    __user__: dict = None,
    __event_call__: callable = None,
    __event_emitter__: callable = None,
) -> str:
    """
    Rename an existing Google Drive file - its content, location, and id are unchanged, only the
    name changes. Use this instead of drive_copy_files or drive_save_edited_copy when the user
    just wants a different name, with nothing else about the file changed.

    Before calling it, send one short line telling the user what you're about to do (e.g.
    "Renaming that file now.") - the popup this tool shows already handles asking permission,
    this is just so the user sees something instead of a blank reply while it loads.

    :param file_id: The Google Drive file id to rename (from drive_search or a previous write tool's result)
    :param name: The new name for the file
    :return: JSON with the file's id and new name. If the chat already shows a card for this file it
        now shows the new name, and no new card is added - so just tell the user the file was renamed
    """

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    try:
        async with httpx.AsyncClient() as client:
            metadata, error = await _drive_fetch_metadata(client, headers, file_id, 'name,parents', 'rename this file')
            if error:
                return json.dumps({'error': error})

            source_name = metadata.get('name', 'this file')
            if source_name == name:
                return json.dumps({'error': f'"{source_name}" is already named "{name}".'})

            parent_id = (metadata.get('parents') or [None])[0]
            duplicate_exists = await _drive_name_exists(client, headers, name, parent_id=parent_id)
    except Exception as e:
        log.exception(f'drive_rename_file lookup error: {e}')
        return json.dumps({'error': str(e)})

    message = f'Rename "{source_name}" to "{name}"?'
    if duplicate_exists:
        message += ' A file with this name already exists in the same folder.'

    confirmed = await __event_call__(
        {
            'type': 'confirmation',
            'data': {
                'title': 'Rename Google Drive file?',
                'message': message,
                'action': 'drive_rename_file',
                'allow_remember': True,
            },
        }
    )
    if confirmed is not True:
        return _drive_cancelled('rename this file', 'Tell the user the file was not renamed')

    try:
        async with httpx.AsyncClient() as client:
            f, error = await _drive_call(
                client,
                'PATCH',
                f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}',
                headers,
                'Google Drive rename failed',
                'Failed to rename this file.',
                params={'supportsAllDrives': 'true', 'fields': 'id,name,mimeType,webViewLink'},
                json={'name': name},
            )
        if error:
            return error

        await _drive_emit_document_card(__event_emitter__, f, update_only=True)
        return json.dumps({'status': 'success', 'id': f['id'], 'name': f['name']}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'drive_rename_file error: {e}')
        return json.dumps({'error': str(e)})


async def drive_copy_files(
    file_ids: list[str],
    folder: str = '',
    __user__: dict = None,
    __event_call__: callable = None,
    __event_emitter__: callable = None,
) -> str:
    """
    Copy one or more Google Drive files (as returned by drive_search), creating new files with
    the same content. Each copy is named "Copy of <original name>".

    Pass every file_id the user wants copied in ONE call, even for a single file - a single
    confirmation covers the whole batch. Only use this for an exact, unmodified duplicate. If the
    user wants to edit or change something, don't call this first - go straight to drive_read
    then drive_save_edited_copy. Chaining copy + save for an edit means two separate confirmation
    prompts and an unwanted, unedited duplicate left behind.

    Before calling it, send one short line telling the user what you're about to do (e.g.
    "Copying those files now.") - the popup this tool shows already handles asking permission,
    this is just so the user sees something instead of a blank reply while it loads.

    :param file_ids: One or more Google Drive file ids to copy
    :param folder: Name of the Drive folder to put the copies in (optional - defaults to each
        file's own folder if omitted). Pass "My Drive" for the top level (Drive's root)
    :return: JSON with which files were copied (id, name, mime_type) and which failed, or an
        error message
    """

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    if not file_ids:
        return json.dumps({'error': 'No files given to copy.'})

    try:
        async with httpx.AsyncClient() as client:
            (metadata_by_id, failed), (folder_id, folder_error) = await asyncio.gather(
                _drive_fetch_metadata_batch(client, headers, file_ids, 'name', 'copy this file'),
                _drive_resolve_optional_folder(headers, folder, client=client),
            )
    except Exception as e:
        log.exception(f'drive_copy_files metadata error: {e}')
        return json.dumps({'error': str(e)})
    if folder_error:
        return json.dumps({'error': folder_error})

    names = {file_id: metadata.get('name', file_id) for file_id, metadata in metadata_by_id.items()}
    if not names:
        return json.dumps({'status': 'error', 'copied': [], 'failed': failed}, ensure_ascii=False)

    title = 'Copy Google Drive file?' if len(names) == 1 else f'Copy {len(names)} files?'
    confirmed = await __event_call__(
        {
            'type': 'confirmation',
            'data': {
                'title': title,
                'message': _drive_batch_message(
                    'Copy {}' + (f' into "{_drive_dialog_text(folder)}"?' if folder else '?'), names
                ),
                'action': 'drive_copy',
                'allow_remember': True,
            },
        }
    )
    if confirmed is not True:
        return _drive_cancelled('copy these files', 'Tell the user the copies were not made')

    async def copy_one(client, file_id):
        # Drive API v3 doesn't auto-prefix "Copy of" like the web UI does - a copy request with
        # no name keeps the exact source name, so it has to be set explicitly here. Drive allows
        # duplicate names, so no need to check for or dodge one - copying the same file twice
        # just makes two files both named "Copy of X", same as Drive's own web UI would.
        body = {'name': f'Copy of {names[file_id]}'}
        if folder_id:
            body['parents'] = [folder_id]

        return await _drive_call_raw(
            client,
            'POST',
            f'{GOOGLE_DRIVE_FILES_URL}/{quote(file_id)}/copy',
            headers,
            'Google Drive copy failed',
            'Failed to copy this file.',
            params={'supportsAllDrives': 'true', 'fields': 'id,name,mimeType,webViewLink'},
            json=body,
        )

    try:
        async with httpx.AsyncClient() as client:
            copied_files, failed = await _drive_run_batch(names, failed, lambda file_id: copy_one(client, file_id))
    except Exception as e:
        log.exception(f'drive_copy_files error: {e}')
        return json.dumps({'error': str(e)})

    for f in copied_files:
        await _drive_emit_document_card(__event_emitter__, f)

    result = {
        'status': _batch_status(copied_files, failed),
        'copied': [{'id': f['id'], 'name': f['name'], 'mime_type': f['mimeType']} for f in copied_files],
        'failed': failed,
    }
    if copied_files:
        result['note'] = DRIVE_CARD_NOTE
    return json.dumps(result, ensure_ascii=False)


async def drive_create_files(
    files: list[dict],
    folder: str = '',
    __user__: dict = None,
    __event_call__: callable = None,
    __event_emitter__: callable = None,
) -> str:
    """
    Create one or more brand new PLAIN TEXT files in the current user's Google Drive, with no
    relation to existing files. If the user asks for a Word/docx, PDF, Excel/xlsx, or
    PowerPoint/pptx file, use create_documents instead (then drive_save_documents if it should go
    in Drive) - not this tool - so it's created in that actual format instead of becoming a plain
    .txt file. If the user is asking to edit, update, or fix an existing file, use
    drive_save_edited_copy instead of either of these. If the
    user wants a FOLDER (not a file with content), use drive_create_folders instead - not this
    tool - a folder is not a plain text file.

    Pass every file the user wants created in ONE call, even for a single file - a single
    confirmation covers the whole batch. All files are created in the same folder.

    Before calling it, send one short line telling the user what you're about to do (e.g.
    "Creating that file now.") - the popup this tool shows already handles asking permission,
    this is just so the user sees something instead of a blank reply while it loads.

    :param files: One or more files to create, each shaped {"name": "...", "content": "..."
        (optional, empty file if omitted), "mime_type": "..." (optional, default text/plain)}
    :param folder: Name of the Drive folder to create the files in (optional - defaults to the
        top level of My Drive if omitted)
    :return: JSON with which files were created (id, name, mime_type) and which failed, or an
        error message
    """

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    if not files:
        return json.dumps({'error': 'No files given to create.'})

    async with httpx.AsyncClient() as client:
        folder_id, folder_error = await _drive_resolve_optional_folder(headers, folder, client=client)
        if folder_error:
            return json.dumps({'error': folder_error})

    names = {i: f['name'] for i, f in enumerate(files)}
    title = 'Create Google Drive file?' if len(files) == 1 else f'Create {len(files)} files?'
    message = _drive_batch_message('Create {}' + (f' in "{_drive_dialog_text(folder)}"?' if folder else '?'), names)

    confirmed = await __event_call__(
        {
            'type': 'confirmation',
            'data': {
                'title': title,
                'message': message,
                'action': 'drive_create_files',
                'allow_remember': True,
            },
        }
    )
    if confirmed is not True:
        return _drive_cancelled('create these files', 'Tell the user the files were not created')

    try:
        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                *(
                    _drive_create_plain_file(
                        client, headers, f['name'], f.get('content', ''), f.get('mime_type', 'text/plain'), parent_id=folder_id
                    )
                    for f in files
                )
            )
    except Exception as e:
        log.exception(f'drive_create_files error: {e}')
        return json.dumps({'error': str(e)})

    created, failed = [], []
    for f, (data, create_error) in zip(files, results):
        if create_error:
            failed.append({'name': f['name'], 'error': create_error})
        else:
            created.append({'id': data['id'], 'name': data['name'], 'mime_type': data['mimeType']})
            await _drive_emit_document_card(__event_emitter__, data)

    result = {'status': _batch_status(created, failed), 'created': created, 'failed': failed}
    if created:
        result['note'] = DRIVE_CARD_NOTE
    return json.dumps(result, ensure_ascii=False)


class DriveSaveError(Exception):
    pass


DRIVE_ALREADY_SAVED_NOTE = (
    'Documents with already_saved were in the user\'s Drive from before, so no new copy was made - tell the user '
    'they were already there instead of saying they were just saved.'
)


def drive_document_card(saved: dict) -> dict:
    """A saved document as its chat card, without the already_saved flag that only the model needs."""
    return {k: v for k, v in saved.items() if k != 'already_saved'}


def _drive_saved_document(document: dict, drive_id: str, web_link: str | None) -> dict:
    return {
        'file_id': document['file_id'],
        'name': document['name'],
        'format': document['format'],
        'drive_id': drive_id,
        'web_link': web_link,
    }


async def _drive_save_stored_document(client, headers: dict, document: dict, folder_id: str | None) -> dict:
    """Upload one stored document to Drive, reusing its earlier Drive copy if it exists and no folder was asked for."""
    from pathlib import Path

    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    saved = document.get('drive')
    if saved and not folder_id:
        response = await _drive_request(
            client,
            'GET',
            f'{GOOGLE_DRIVE_FILES_URL}/{saved["id"]}',
            headers,
            params={'fields': 'id,trashed', 'supportsAllDrives': 'true'},
        )
        if response.status_code == 200 and not response.json().get('trashed'):
            return {**_drive_saved_document(document, saved['id'], saved.get('web_link')), 'already_saved': True}
        # Only a deleted or trashed copy is replaced, any other error would otherwise upload a duplicate
        if response.status_code not in (200, 404):
            log.error(f'Google Drive saved copy check failed: {response.status_code} {response.text}')
            raise DriveSaveError(_drive_error_message(response, "Couldn't check this document's Google Drive copy."))

    file_path = await asyncio.to_thread(Storage.get_file, document['path'])
    file_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
    response = await _drive_upload_document_bytes(
        client, headers, document['name'], document['format'], file_bytes, parent_id=folder_id
    )
    if response.status_code != 200:
        log.error(f'Google Drive document save failed: {response.status_code} {response.text}')
        raise DriveSaveError(_drive_error_message(response, "Couldn't save this document to Google Drive."))

    data = response.json()
    await Files.update_file_metadata_by_id(
        document['file_id'], {'drive': {'id': data['id'], 'web_link': data.get('webViewLink')}}
    )
    return _drive_saved_document(document, data['id'], data.get('webViewLink'))


async def save_stored_documents_to_drive(
    headers: dict, documents: list[dict], folder_id: str | None = None
) -> tuple[list[dict], list[dict]]:
    """Save each stored document to Drive on its own, as (saved, failed)."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_drive_save_stored_document(client, headers, d, folder_id) for d in documents), return_exceptions=True
        )

    saved, failed = [], []
    for document, result in zip(documents, results):
        if isinstance(result, DriveSaveError):
            failed.append({'name': _document_display_name(document), 'error': str(result)})
        elif isinstance(result, BaseException):
            log.error(f'Google Drive document save error: {result}', exc_info=result)
            failed.append(
                {
                    'name': _document_display_name(document),
                    'error': "Couldn't reach Google Drive - try again in a moment.",
                }
            )
        else:
            saved.append(result)
    return saved, failed


async def drive_save_documents(
    file_ids: list[str],
    folder: str = '',
    __user__: dict = None,
    __event_call__: callable = None,
    __event_emitter__: callable = None,
) -> str:
    """
    Save documents made with create_documents to the current user's Google Drive. Word/Excel/PowerPoint
    are saved as native Google Docs/Sheets/Slides, so the user can open and edit them in Drive. Only
    call this when the user asks for the documents in Drive - create_documents already lets them
    preview and download without it, and each card can add its document to Drive on its own.

    To put a brand new document in Drive, call create_documents first and then this tool with the
    file_ids it returned. Pass every document to save in ONE call - a single confirmation covers the
    whole batch. If the user is asking to edit an existing Drive file, use drive_save_edited_copy instead.

    Before calling it, send one short line telling the user what you're about to do (e.g.
    "Saving that to your Drive now.") - the popup this tool shows already handles asking permission,
    this is just so the user sees something instead of a blank reply while it loads.

    :param file_ids: The file_id of each document to save, as returned by create_documents
    :param folder: Name of the Drive folder to save the documents in (optional - defaults to the top
        level of My Drive, or to wherever a document was already saved)
    :return: JSON with which documents were saved (file_id, drive_id, name), which failed and why -
        each document succeeds or fails on its own - and a `message` summarising it for the user
    """
    if not file_ids:
        return json.dumps({'error': 'No documents given to save.'})

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    documents, failed = await load_stored_documents(__user__['id'], list(dict.fromkeys(file_ids)))
    if not documents:
        return _documents_result([], failed, action='save')

    async with httpx.AsyncClient() as client:
        folder_id, folder_error = await _drive_resolve_optional_folder(headers, folder, client=client)
        if folder_error:
            return json.dumps({'error': folder_error})

    names = {i: _document_display_name(d) for i, d in enumerate(documents)}
    title = 'Save to Google Drive?' if len(documents) == 1 else f'Save {len(documents)} files to Google Drive?'
    destination = f' to "{_drive_dialog_text(folder)}"?' if folder else ' to your Drive?'
    message = _drive_batch_message('Save {}' + destination, names)

    confirmed = await __event_call__(
        {
            'type': 'confirmation',
            'data': {
                'title': title,
                'message': message,
                'action': 'drive_create_files',
                'allow_remember': True,
            },
        }
    )
    if confirmed is not True:
        return _drive_cancelled('save these documents to Drive', 'Tell the user the documents were not saved to Drive')

    saved, save_failed = await save_stored_documents_to_drive(headers, documents, folder_id)
    await _emit_document_cards(__event_emitter__, [drive_document_card(s) for s in saved])

    note = DRIVE_CARD_NOTE
    if any(s.get('already_saved') for s in saved):
        note += f' {DRIVE_ALREADY_SAVED_NOTE}'
    return _documents_result(saved, failed + save_failed, success_note=note, action='save')


def _drive_folder_creation_levels(folders: list[dict]):
    """Group folders into ordered levels so each folder's in-batch parent (if any) is created in
    an earlier level - folders with no dependency, or whose parent isn't in this batch, go first.
    Raises ValueError on a circular parent reference."""
    remaining = {f['name']: f for f in folders}
    levels = []
    while remaining:
        level = [f for f in remaining.values() if not f.get('parent') or f['parent'] not in remaining]
        if not level:
            raise ValueError('Circular parent reference among the folders to create.')
        levels.append(level)
        for f in level:
            del remaining[f['name']]
    return levels


async def drive_create_folders(
    folders: list[dict],
    folder: str = '',
    __user__: dict = None,
    __event_call__: callable = None,
) -> str:
    """
    Create one or more new folders in the current user's Google Drive. Use this - not
    drive_create_files or create_documents - whenever the user asks for a folder; a folder
    is not a file, plain text or otherwise.

    Pass every folder the user wants created in ONE call, even for a single folder - a single
    confirmation covers the whole batch. Folders can be created side by side (independent, made
    concurrently), or nested inside each other in the same call by setting a folder's "parent" to
    another folder's "name" in this same list - e.g. to create "2024" then "Q1" inside it: pass
    [{"name": "2024"}, {"name": "Q1", "parent": "2024"}].

    Before calling it, send one short line telling the user what you're about to do (e.g.
    "Creating that folder now.") - the popup this tool shows already handles asking permission,
    this is just so the user sees something instead of a blank reply while it loads.

    :param folders: One or more folders to create, each shaped {"name": "...", "parent": "..."
        (optional - the exact "name" of another folder in this same list to nest inside; leave
        empty or omit to create directly under `folder`)}
    :param folder: Name of the existing Drive folder these new folders should be created under
        (optional - defaults to the top level of My Drive if omitted). Pass "My Drive" for the
        top level explicitly
    :return: JSON with which folders were created (id, name) and which failed, or an error message
    """
    if not folders:
        return json.dumps({'error': 'No folders given to create.'})

    names = [f.get('name') for f in folders]
    if not all(names) or len(set(names)) != len(names):
        return json.dumps({'error': 'Every folder needs its own, non-empty name.'})
    for f in folders:
        if f.get('parent') == f['name']:
            return json.dumps({'error': f'"{f["name"]}" can\'t be its own parent.'})
        if f.get('parent') and f['parent'] not in names:
            return json.dumps({'error': f'"{f["parent"]}" is not one of the folders in this same request.'})

    try:
        levels = _drive_folder_creation_levels(folders)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    headers, error = await _drive_prepare_write(__user__, __event_call__)
    if error:
        return error

    async with httpx.AsyncClient() as client:
        folder_id, folder_error = await _drive_resolve_optional_folder(headers, folder, client=client)
        if folder_error:
            return json.dumps({'error': folder_error})

    display_names = {
        i: f['name'] + (f' (in "{f["parent"]}")' if f.get('parent') else '') for i, f in enumerate(folders)
    }
    title = 'Create Google Drive folder?' if len(folders) == 1 else f'Create {len(folders)} folders?'
    message = _drive_batch_message(
        'Create {}' + (f' in "{_drive_dialog_text(folder)}"?' if folder else '?'), display_names
    )

    confirmed = await __event_call__(
        {
            'type': 'confirmation',
            'data': {
                'title': title,
                'message': message,
                'action': 'drive_create_folders',
                'allow_remember': True,
            },
        }
    )
    if confirmed is not True:
        return _drive_cancelled('create these folders', 'Tell the user the folders were not created')

    created_ids: dict[str, str] = {}
    failed_names: set = set()
    created, failed = [], []
    try:
        async with httpx.AsyncClient() as client:
            for level in levels:
                pending = [f for f in level if not f.get('parent') or f['parent'] not in failed_names]
                for f in level:
                    if f not in pending:
                        failed.append({'name': f['name'], 'error': f'Skipped - parent "{f["parent"]}" failed to create.'})
                        failed_names.add(f['name'])
                if not pending:
                    continue

                results = await asyncio.gather(
                    *(
                        _drive_create_plain_file(
                            client,
                            headers,
                            f['name'],
                            '',
                            'application/vnd.google-apps.folder',
                            parent_id=created_ids.get(f.get('parent'), folder_id),
                        )
                        for f in pending
                    )
                )
                for f, (data, create_error) in zip(pending, results):
                    if create_error:
                        failed.append({'name': f['name'], 'error': create_error})
                        failed_names.add(f['name'])
                    else:
                        created_ids[f['name']] = data['id']
                        created.append({'id': data['id'], 'name': data['name']})
    except Exception as e:
        log.exception(f'drive_create_folders error: {e}')
        return json.dumps({'error': str(e)})

    return json.dumps(
        {'status': _batch_status(created, failed), 'created': created, 'failed': failed},
        ensure_ascii=False,
    )
