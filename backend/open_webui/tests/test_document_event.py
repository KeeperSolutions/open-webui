from unittest.mock import AsyncMock, patch

import pytest

import open_webui.socket.main as socket_main

REQUEST_INFO = {'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-1'}


async def emit_document(existing_message, document):
    upsert = AsyncMock()
    with (
        patch.object(socket_main.sio, 'emit', AsyncMock()),
        patch.object(socket_main.Chats, 'get_message_by_id_and_message_id', AsyncMock(return_value=existing_message)),
        patch.object(socket_main.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert),
    ):
        emitter = await socket_main.get_event_emitter(REQUEST_INFO)
        await emitter({'type': 'chat:message:document', 'data': document})
    return upsert


@pytest.mark.asyncio
async def test_document_event_is_appended_to_the_message():
    existing = {'documents': [{'file_id': 'file-1', 'name': 'Report', 'format': 'docx'}]}
    upsert = await emit_document(existing, {'file_id': 'file-2', 'name': 'Data', 'format': 'xlsx'})

    upsert.assert_awaited_once_with(
        'chat-1',
        'message-1',
        {
            'documents': [
                {'file_id': 'file-1', 'name': 'Report', 'format': 'docx'},
                {'file_id': 'file-2', 'name': 'Data', 'format': 'xlsx'},
            ]
        },
        touch=False,
    )


@pytest.mark.asyncio
async def test_repeated_document_event_replaces_the_same_file_instead_of_duplicating():
    existing = {'documents': [{'file_id': 'file-1', 'name': 'Old', 'format': 'docx'}]}
    upsert = await emit_document(existing, {'file_id': 'file-1', 'name': 'Report', 'format': 'docx'})

    assert upsert.await_args.args[2] == {'documents': [{'file_id': 'file-1', 'name': 'Report', 'format': 'docx'}]}
