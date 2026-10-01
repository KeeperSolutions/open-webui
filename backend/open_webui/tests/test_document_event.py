from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import open_webui.socket.main as socket_main

REQUEST_INFO = {'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-2'}


async def emit_document(messages, document, event_type='chat:message:document', rename=None):
    upsert = AsyncMock()
    chat = SimpleNamespace(chat={'history': {'messages': messages}})
    with (
        patch.object(socket_main.sio, 'emit', AsyncMock()),
        patch.object(socket_main.Chats, 'get_chat_by_id', AsyncMock(return_value=chat)),
        patch.object(socket_main.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert),
        patch.object(socket_main.Files, 'update_file_name_by_id', rename or AsyncMock()),
    ):
        emitter = await socket_main.get_event_emitter(REQUEST_INFO)
        await emitter({'type': event_type, 'data': document})
    return upsert


@pytest.mark.asyncio
async def test_document_event_is_appended_to_the_message():
    messages = {'message-2': {'documents': [{'file_id': 'file-1', 'name': 'Report', 'format': 'docx'}]}}
    upsert = await emit_document(messages, {'file_id': 'file-2', 'name': 'Data', 'format': 'xlsx'})

    upsert.assert_awaited_once_with(
        'chat-1',
        'message-2',
        {
            'documents': [
                {'file_id': 'file-1', 'name': 'Report', 'format': 'docx'},
                {'file_id': 'file-2', 'name': 'Data', 'format': 'xlsx'},
            ]
        },
        touch=False,
    )


@pytest.mark.asyncio
async def test_repeated_document_event_updates_the_same_file_instead_of_duplicating():
    messages = {'message-2': {'documents': [{'file_id': 'file-1', 'name': 'Old', 'format': 'docx'}]}}
    upsert = await emit_document(messages, {'file_id': 'file-1', 'name': 'Report', 'format': 'docx'})

    assert upsert.await_args.args[2] == {'documents': [{'file_id': 'file-1', 'name': 'Report', 'format': 'docx'}]}


@pytest.mark.asyncio
async def test_document_from_an_earlier_message_updates_that_message_instead_of_adding_a_new_card():
    messages = {
        'message-1': {'documents': [{'file_id': 'file-1', 'name': 'Report', 'format': 'docx'}]},
        'message-2': {},
    }
    upsert = await emit_document(
        messages, {'file_id': 'file-1', 'name': 'Report', 'format': 'docx', 'drive_id': 'd-1', 'web_link': 'link'}
    )

    upsert.assert_awaited_once_with(
        'chat-1',
        'message-1',
        {
            'documents': [
                {'file_id': 'file-1', 'name': 'Report', 'format': 'docx', 'drive_id': 'd-1', 'web_link': 'link'}
            ]
        },
        touch=False,
    )


@pytest.mark.asyncio
async def test_update_only_event_for_a_file_without_a_card_adds_nothing():
    upsert = await emit_document(
        {'message-2': {}},
        {'drive_id': 'd-9', 'name': 'Elsewhere', 'format': 'docx'},
        event_type='chat:message:document:update',
    )

    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_renaming_the_drive_copy_of_a_stored_document_renames_its_card_and_file():
    messages = {
        'message-1': {
            'documents': [{'file_id': 'file-1', 'name': 'abc', 'format': 'docx', 'drive_id': 'd-1', 'web_link': 'link'}]
        },
        'message-2': {},
    }
    rename = AsyncMock()
    upsert = await emit_document(
        messages,
        {'drive_id': 'd-1', 'name': '123', 'format': 'docx', 'web_link': 'link'},
        event_type='chat:message:document:update',
        rename=rename,
    )

    rename.assert_awaited_once_with('file-1', '123.docx')
    upsert.assert_awaited_once_with(
        'chat-1',
        'message-1',
        {'documents': [{'file_id': 'file-1', 'name': '123', 'format': 'docx', 'drive_id': 'd-1', 'web_link': 'link'}]},
        touch=False,
    )


@pytest.mark.asyncio
async def test_drive_only_documents_are_matched_by_their_drive_id():
    messages = {
        'message-1': {'documents': [{'drive_id': 'd-1', 'name': 'Copy', 'format': 'xlsx', 'web_link': 'a'}]},
        'message-2': {},
    }
    upsert = await emit_document(messages, {'drive_id': 'd-1', 'name': 'Copy', 'format': 'xlsx', 'web_link': 'b'})

    assert upsert.await_args.args[1] == 'message-1'
    assert upsert.await_args.args[2] == {
        'documents': [{'drive_id': 'd-1', 'name': 'Copy', 'format': 'xlsx', 'web_link': 'b'}]
    }


@pytest.mark.asyncio
async def test_concurrent_card_updates_on_one_message_do_not_overwrite_each_other():
    import asyncio
    import copy

    stored = {
        'message-1': {
            'documents': [
                {'file_id': 'file-1', 'name': 'A', 'format': 'docx'},
                {'file_id': 'file-2', 'name': 'B', 'format': 'xlsx'},
            ]
        }
    }

    async def get_chat(chat_id):
        snapshot = copy.deepcopy(stored)
        # Lets the other update read the same state before this one writes, like two requests hitting the DB
        await asyncio.sleep(0)
        return SimpleNamespace(chat={'history': {'messages': snapshot}})

    async def upsert(chat_id, message_id, message, touch):
        stored[message_id] = {**stored.get(message_id, {}), **message}

    with (
        patch.object(socket_main.Chats, 'get_chat_by_id', get_chat),
        patch.object(socket_main.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert),
    ):
        await asyncio.gather(
            socket_main.upsert_document_card('chat-1', 'message-1', {'file_id': 'file-1', 'drive_id': 'd-1'}, True),
            socket_main.upsert_document_card('chat-1', 'message-1', {'file_id': 'file-2', 'drive_id': 'd-2'}, True),
        )

    assert [d.get('drive_id') for d in stored['message-1']['documents']] == ['d-1', 'd-2']
    assert 'chat-1' not in socket_main.DOCUMENT_CARD_LOCKS
