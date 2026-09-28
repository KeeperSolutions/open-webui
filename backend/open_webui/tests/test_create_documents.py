import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import open_webui.tools.built_in as built_in

USER = {
    'id': 'user-1',
    'email': 'user@example.com',
    'name': 'User',
    'role': 'user',
    'profile_image_url': '',
    'last_active_at': 0,
    'updated_at': 0,
    'created_at': 0,
}


def fake_build(format, name, content):
    if name == 'Broken':
        raise ValueError('bad content')
    return b'bytes'


async def create(files, upload=None, chat_id='chat-1', emitted=None):
    stored = []

    async def default_upload(request, file, metadata, process, user):
        stored.append({'filename': file.filename, 'content_type': file.content_type, 'process': process})
        return SimpleNamespace(id=f'file-{len(stored)}')

    async def emit(event):
        emitted.append(event)

    insert_chat_files = AsyncMock()
    with (
        patch.object(built_in, '_build_document_bytes', fake_build),
        patch('open_webui.routers.files.upload_file_handler', upload or default_upload),
        patch.object(built_in.Chats, 'insert_chat_files', insert_chat_files),
    ):
        result = json.loads(
            await built_in.create_documents(
                files=files,
                __request__=object(),
                __user__=USER,
                __event_emitter__=emit if emitted is not None else None,
                __chat_id__=chat_id,
                __message_id__='message-1',
            )
        )
    return result, stored, insert_chat_files


@pytest.mark.asyncio
async def test_stores_each_document_links_it_to_the_chat_and_emits_a_card():
    emitted = []
    result, stored, insert_chat_files = await create(
        [{'name': 'Report', 'format': 'docx', 'content': '# Hi'}, {'name': 'Data', 'format': 'xlsx', 'content': 'a,b'}],
        emitted=emitted,
    )

    assert result['status'] == 'success'
    assert result['created'] == [
        {'file_id': 'file-1', 'name': 'Report', 'format': 'docx'},
        {'file_id': 'file-2', 'name': 'Data', 'format': 'xlsx'},
    ]
    assert result['note'] == built_in.DOCUMENT_CARD_NOTE
    assert [s['filename'] for s in stored] == ['Report.docx', 'Data.xlsx']
    assert stored[0]['content_type'] == built_in.DOCUMENT_MIME_TYPES['docx']
    assert all(s['process'] is False for s in stored)
    insert_chat_files.assert_awaited_once_with(
        chat_id='chat-1', message_id='message-1', file_ids=['file-1', 'file-2'], user_id='user-1'
    )
    assert [e['type'] for e in emitted] == ['chat:message:document'] * 2
    assert emitted[0]['data'] == {'file_id': 'file-1', 'name': 'Report', 'format': 'docx'}


@pytest.mark.asyncio
async def test_one_failed_build_or_store_does_not_fail_the_rest():
    stored = []

    async def upload(request, file, metadata, process, user):
        if file.filename == 'Huge.pdf':
            raise HTTPException(status_code=413, detail='File too large')
        stored.append(file.filename)
        return SimpleNamespace(id='file-ok')

    result, _, _ = await create(
        [
            {'name': 'Report', 'format': 'docx', 'content': '# Hi'},
            {'name': 'Broken', 'format': 'xlsx', 'content': 'a,b'},
            {'name': 'Huge', 'format': 'pdf', 'content': 'x'},
            {'name': 'Notes', 'format': 'odt', 'content': 'x'},
        ],
        upload=upload,
    )

    assert result['status'] == 'partial'
    assert stored == ['Report.docx']
    assert [f['name'] for f in result['failed']] == ['Notes.odt', 'Broken.xlsx', 'Huge.pdf']
    assert result['failed'][2]['error'] == "Couldn't save this document - File too large"
    assert built_in.DOCUMENT_FAILURES_NOTE in result['note']


@pytest.mark.asyncio
async def test_temporary_chat_skips_chat_file_linking():
    result, _, insert_chat_files = await create(
        [{'name': 'Report', 'format': 'docx', 'content': '# Hi'}], chat_id='temporary:chat-1'
    )

    assert result['status'] == 'success'
    insert_chat_files.assert_not_awaited()
