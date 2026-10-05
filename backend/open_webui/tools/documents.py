"""
Built-in tool that creates documents (PDF, Word, Excel, PowerPoint) the user can preview and download in the chat.

Exposed to models through get_builtin_tools, like the tools in built_in.py.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import Field
from typing_extensions import TypedDict

from open_webui.env import FONTS_DIR
from open_webui.models.chats import Chats
from open_webui.models.users import UserModel
from open_webui.utils import document_builders
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.rate_limit import RateLimiter
from open_webui.utils.redis import get_redis_client

log = logging.getLogger(__name__)


# The formats create_documents can build, the single list every other format check is derived from
DOCUMENT_MIME_TYPES = {
    'pdf': 'application/pdf',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}
DOCUMENT_FORMATS = tuple(DOCUMENT_MIME_TYPES)
DOCUMENT_FORMATS_TEXT = f'{", ".join(DOCUMENT_FORMATS[:-1])}, or {DOCUMENT_FORMATS[-1]}'

# Largest document create_documents will store
DOCUMENT_MAX_BYTES = 25_000_000

# Most documents create_documents builds in one call
DOCUMENT_MAX_PER_CALL = 20

# Most documents one user can create per hour
DOCUMENT_MAX_PER_HOUR = 40

# Longest text, in characters, accepted for one document
DOCUMENT_MAX_CONTENT_CHARS = 500_000

_document_rate_limiter = RateLimiter(redis_client=get_redis_client(), limit=DOCUMENT_MAX_PER_HOUR, window=3600)

# Documents built at the same time across the whole server, the rest wait their turn
DOCUMENT_BUILD_CONCURRENCY = 3
_document_build_slots = asyncio.Semaphore(DOCUMENT_BUILD_CONCURRENCY)

# A build that runs longer than this is killed
DOCUMENT_BUILD_TIMEOUT_SECONDS = 60

DOCUMENT_BUILD_COMMAND = [sys.executable, '-m', 'open_webui.utils.document_builders']


async def _emit_document_cards(event_emitter, documents: list[dict], update_only: bool = False):
    """Show each document as a card in the chat, or with update_only just refresh the card it already has."""
    if not event_emitter:
        return
    for document in documents:
        await event_emitter(
            {'type': 'chat:message:document:update' if update_only else 'chat:message:document', 'data': document}
        )


class DocumentSpec(TypedDict):
    name: Annotated[
        str,
        Field(
            description='Document title shown to the user and used as the file name, in the language of the content - '
            'normal words with spaces and diacritics (e.g. "Kratki izvještaj", not "Kratki_izvjestaj"), '
            'no file extension'
        ),
    ]
    format: Literal[DOCUMENT_FORMATS]
    content: Annotated[
        str,
        Field(
            description='For pdf/docx: markdown text. For xlsx: CSV text (rows on new lines, columns comma-separated). '
            "For pptx: slides separated by '---', the first line of each is the title, "
            'the remaining lines are bullet points'
        ),
    ]


# Told to the model when a document's content is empty or can't be built, so it can fix the content and retry
DOCUMENT_CONTENT_HINTS = {
    'pdf': 'content must be markdown text',
    'docx': 'content must be markdown text',
    'xlsx': 'content must be CSV text (rows on new lines, columns comma-separated)',
    'pptx': "content must be slides separated by '---', each starting with a title line",
}

DOCUMENT_FAILURES_NOTE = (
    'Tell the user which documents were not {done} and why, based on `message`, in the language of the '
    "conversation - translate it if needed, but keep each file name and reason exact, don't soften or guess at them."
)
DOCUMENT_LIMIT_REACHED_NOTE = (
    'The hourly limit was reached: say in one sentence that no more documents can be created right now and how many '
    "were left out, in the conversation's language - do not list the documents that were left out."
)
DOCUMENT_MISSING_ERROR = 'This document no longer exists.'


def _document_display_name(f: dict) -> str:
    name = (f.get('name') or '').strip() or 'Untitled'
    return f'{name}.{f["format"]}' if f.get('format') else name


def _document_input_error(f: dict) -> str | None:
    """Why this document can't be built from its inputs, or None if they look usable."""
    if f['format'] not in DOCUMENT_MIME_TYPES:
        return f'Missing or unsupported "format" ("{f["format"]}") - set "format" to {DOCUMENT_FORMATS_TEXT}.'
    if not (f.get('name') or '').strip():
        return 'Missing a file name.'
    if not (f.get('content') or '').strip():
        return f'Empty content - {DOCUMENT_CONTENT_HINTS[f["format"]]}.'
    if len(f['content']) > DOCUMENT_MAX_CONTENT_CHARS:
        return f'The content is longer than {DOCUMENT_MAX_CONTENT_CHARS:,} characters - split it into several documents.'
    return None


class DocumentBuildError(Exception):
    pass


class DocumentBuildTimeout(DocumentBuildError):
    pass


class DocumentTooLarge(DocumentBuildError):
    pass


def _document_too_large_message() -> str:
    return f'The file is larger than {DOCUMENT_MAX_BYTES // 1_000_000} MB - split the content into smaller documents.'


def _document_build_failure_message(format: str, error: Exception) -> str:
    """What to tell the user about a failed build, without the child's traceback."""
    if isinstance(error, DocumentBuildTimeout):
        return (
            f'Building this {format} file took longer than {DOCUMENT_BUILD_TIMEOUT_SECONDS} seconds - '
            'shorten the content or split it into several documents.'
        )
    if isinstance(error, DocumentTooLarge):
        return _document_too_large_message()
    return f"Couldn't build this {format} file - {DOCUMENT_CONTENT_HINTS[format]}."


def _kill_process_tree(process):
    """Kill a build process together with what it started, like pandoc."""
    try:
        if hasattr(os, 'killpg'):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


async def _build_document_bytes(format: str, name: str, content: str) -> bytes:
    """Build one document in a child process, which is killed if it runs past the timeout."""
    spec = {'format': format, 'name': name, 'content': content, 'fonts_dir': str(FONTS_DIR)}
    spec['max_bytes'] = DOCUMENT_MAX_BYTES
    if format == 'docx':
        reference_doc = await asyncio.to_thread(document_builders._get_pandoc_reference_doc)
        spec['reference_doc'] = str(reference_doc) if reference_doc else None
    # The child finds open_webui where the server does, whichever directory the server was started from
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(document_builders.__file__))))
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(p for p in (package_root, os.environ.get('PYTHONPATH')) if p)}
    # A killed child can't clean up its temp files, so they go in a directory removed here
    with tempfile.TemporaryDirectory(prefix='document-build-') as tmp_dir:
        spec['tmp_dir'] = tmp_dir
        payload = json.dumps(spec).encode()
        async with _document_build_slots:
            process = await asyncio.create_subprocess_exec(
                *DOCUMENT_BUILD_COMMAND,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(payload), DOCUMENT_BUILD_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                _kill_process_tree(process)
                await process.wait()
                raise DocumentBuildTimeout(f'The {format} build took longer than {DOCUMENT_BUILD_TIMEOUT_SECONDS} seconds.')
            except BaseException:
                # Also covers the request being cancelled, so no build is left running for nobody
                _kill_process_tree(process)
                raise
    if process.returncode == document_builders.EXIT_TOO_LARGE:
        raise DocumentTooLarge(_document_too_large_message())
    if process.returncode != 0:
        raise DocumentBuildError(f'The {format} build exited with {process.returncode}: {stderr.decode(errors="replace")[-2000:]}')
    return stdout


async def _build_document_safe(f: dict) -> tuple[bytes | None, str | None]:
    """Build one document as (bytes, None) or (None, error), isolated from its batch."""
    try:
        return await _build_document_bytes(f['format'], f['name'], f['content']), None
    except Exception as e:
        log.error(f'Document build failed for {_document_display_name(f)}: {e}')
        return None, _document_build_failure_message(f['format'], e)


def _documents_failures_text(failed: list) -> str:
    """Why documents failed, with the ones that failed for the same reason in a single sentence."""
    names_by_error: dict[str, list[str]] = {}
    left_out: list[dict] = []
    for f in failed:
        if f.get('limit_reached'):
            left_out.append(f)
        else:
            names_by_error.setdefault(f['error'], []).append(f['name'])

    parts = []
    for error, names in names_by_error.items():
        if len(names) == 1:
            parts.append(f'{names[0]}: {error}')
        else:
            # A few names still help, a long list of them only repeats the same sentence
            which = f' ({", ".join(names)})' if len(names) <= 3 else ''
            parts.append(f'{len(names)} documents{which}: {error}')
    if left_out:
        # Running out of the hour is the user's situation and not a fault of those documents, so no names
        plural = 's' if len(left_out) != 1 else ''
        parts.append(f'{len(left_out)} document{plural} left out: {left_out[0]["error"]}')
    return ' '.join(parts)


def _documents_summary_message(created: list, failed: list, action: str = 'create') -> str:
    total = len(created) + len(failed)
    done = f'{action.capitalize()}d'
    if not failed:
        return f'{done} {total} document{"s" if total != 1 else ""}.'
    not_done = _documents_failures_text(failed)
    if not created:
        return f"Couldn't {action} {'the document' if total == 1 else f'any of the {total} documents'}. {not_done}"
    return f'{done} {len(created)} of {total} documents. Not {done.lower()} - {not_done}'


def _documents_result(done: list, failed: list, success_note: str = '', action: str = 'create') -> str:
    result = {
        'status': _batch_status(done, failed),
        f'{action}d': done,
        'failed': failed,
        'message': _documents_summary_message(done, failed, action),
    }
    failures_note = DOCUMENT_FAILURES_NOTE.format(done=f'{action}d')
    limit_note = DOCUMENT_LIMIT_REACHED_NOTE if any(f.get('limit_reached') for f in failed) else ''
    note = ' '.join(n for n in (failures_note if failed else '', limit_note, success_note if done else '') if n)
    if note:
        result['note'] = note
    return json.dumps(result, ensure_ascii=False)


def _batch_status(succeeded: list, failed: list) -> str:
    """'success' if nothing failed, 'error' if nothing succeeded, 'partial' if it's a mix."""
    if not failed:
        return 'success'
    return 'error' if not succeeded else 'partial'


DOCUMENT_CARD_NOTE = (
    'A card that previews and downloads each created document is already shown to the user - do not include a link '
    'in your reply. The documents are not in Google Drive; only if the user asks to save them there, call '
    'drive_save_documents with their file_ids.'
)


async def _store_document(
    request: Request, user: UserModel, f: dict, file_bytes: bytes
) -> tuple[str | None, str | None]:
    """Save one built document as a File owned by the user, as (file_id, None) or (None, error)."""
    import io

    from fastapi import UploadFile

    from open_webui.routers.files import upload_file_handler

    upload = UploadFile(
        file=io.BytesIO(file_bytes),
        filename=_document_display_name(f),
        headers={'content-type': DOCUMENT_MIME_TYPES[f['format']]},
    )
    try:
        file_item = await upload_file_handler(request, file=upload, metadata={}, process=False, user=user, generated=True)
        return file_item.id, None
    except HTTPException as e:
        return None, f"Couldn't save this document - {e.detail}"
    except Exception as e:
        log.exception(f'Storing generated document {_document_display_name(f)} failed: {e}')
        return None, "Couldn't save this document - try again in a moment."


async def load_stored_documents(user_id: str, file_ids: list[str]) -> tuple[list[dict], list[dict]]:
    """Look up documents made by create_documents that belong to this user, as (documents, failed)."""
    from open_webui.models.files import Files

    files = await asyncio.gather(*(Files.get_file_by_id(file_id) for file_id in file_ids), return_exceptions=True)
    documents, failed = [], []
    for file_id, file in zip(file_ids, files):
        if isinstance(file, BaseException):
            log.error(f'Loading stored document {file_id} failed: {file}', exc_info=file)
            failed.append({'name': file_id, 'error': "Couldn't load this document - try again in a moment."})
            continue
        if not file or file.user_id != user_id:
            failed.append({'name': file_id, 'error': DOCUMENT_MISSING_ERROR})
            continue
        stem, _, extension = (file.meta or {}).get('name', file.filename).rpartition('.')
        if not stem or extension.lower() not in DOCUMENT_MIME_TYPES:
            failed.append(
                {'name': file.filename, 'error': f'Only {DOCUMENT_FORMATS_TEXT} documents can be saved to Drive.'}
            )
            continue
        documents.append(
            {
                'file_id': file.id,
                'name': stem,
                'format': extension.lower(),
                'path': file.path,
                'drive': (file.meta or {}).get('drive'),
            }
        )
    return documents, failed


async def _create_document(
    request: Request, user: UserModel, f, limit_error: str | None = None
) -> tuple[dict | None, dict | None]:
    """Validate, build and store one document on its own, as (created, None) or (None, failure)."""
    f = f if isinstance(f, dict) else {}
    # Models sometimes send numbers or lists here, which count as missing instead of crashing the whole batch
    f = {**f, **{key: f[key] if isinstance(f.get(key), str) else '' for key in ('name', 'format', 'content')}}
    f['format'] = f['format'].lower()
    # The upload keeps only what follows the last slash, so "Q1/Q2 Report" would be stored as "Q2 Report"
    f['name'] = f['name'].replace('/', '-').replace('\\', '-')

    error = limit_error
    if not error:
        error = _document_input_error(f)
    if not error:
        file_bytes, error = await _build_document_safe(f)
    if not error and len(file_bytes) > DOCUMENT_MAX_BYTES:
        error = _document_too_large_message()
    if not error:
        file_id, error = await _store_document(request, user, f, file_bytes)
    if error:
        failure = {'name': _document_display_name(f), 'error': error}
        if error == _document_hour_limit_error():
            failure['limit_reached'] = True
        return None, failure
    return {'file_id': file_id, 'name': f['name'], 'format': f['format']}, None


def _document_hour_limit_error() -> str:
    return f'The limit of {DOCUMENT_MAX_PER_HOUR} generated documents per hour is reached - try again later.'


def _document_limit_errors(user_id: str, count: int) -> list[str | None]:
    """For each requested document, why it is over the per-call or per-hour limit, or None if it may be created."""
    per_call_error = f'Only {DOCUMENT_MAX_PER_CALL} documents can be created per call - create this one in a new call.'
    per_hour_error = _document_hour_limit_error()
    errors, hour_limited = [], False
    for index in range(count):
        if index >= DOCUMENT_MAX_PER_CALL:
            # Once the hour is used up that is the reason to give, so every document that is left shares one message
            errors.append(per_hour_error if hour_limited else per_call_error)
            continue
        # Each document takes a place in the user's hour, so it can't be reset by deleting a chat or its files
        hour_limited = hour_limited or _document_rate_limiter.is_limited(user_id)
        errors.append(per_hour_error if hour_limited else None)
    return errors


async def create_documents(
    files: list[DocumentSpec],
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Create one or more documents (PDF, Word, Excel, or PowerPoint) the user can preview and download
    right away in the chat. Use this whenever the user asks for a document, report, spreadsheet, or
    presentation file - it does not need Google Drive.

    Pass every document the user wants in ONE call, even for a single document.

    :param files: One or more documents to create, each with a name, a format, and its content
    :return: JSON with which documents were created (file_id, name, format), which failed and why - each
        document succeeds or fails on its own - and a `message` summarising it for the user
    """
    if __request__ is None or not __user__:
        return json.dumps({'error': 'Request context not available'})
    if not files:
        return json.dumps({'error': 'No documents given to create.'})

    user = UserModel(**__user__)
    limit_errors = _document_limit_errors(user.id, len(files))
    results = await asyncio.gather(*(_create_document(__request__, user, f, e) for f, e in zip(files, limit_errors)))
    created = [document for document, _ in results if document]
    failed = [failure for _, failure in results if failure]

    if created and is_saved_chat_id(__chat_id__) and __message_id__:
        try:
            await Chats.insert_chat_files(
                chat_id=__chat_id__,
                message_id=__message_id__,
                file_ids=[c['file_id'] for c in created],
                user_id=user.id,
            )
        except Exception as e:
            # The files are stored and their cards still work, only the chat's file list misses them
            log.exception(f'Linking created documents to chat {__chat_id__} failed: {e}')

    await _emit_document_cards(__event_emitter__, created)

    return _documents_result(created, failed, success_note=DOCUMENT_CARD_NOTE)
