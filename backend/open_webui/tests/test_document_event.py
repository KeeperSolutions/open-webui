from unittest.mock import AsyncMock, patch

import pytest

import open_webui.socket.main as socket_main

REQUEST_INFO = {'user_id': 'user-1', 'chat_id': 'chat-1', 'message_id': 'message-2'}


def locked_update(messages, upsert):
    # Runs the card update against these messages and records what it would write, like the locked DB update does
    async def update_message_with_lock(chat_id, build, touch=True):
        update = build(messages)
        if update is not None:
            await upsert(chat_id, *update, touch=touch)

    return update_message_with_lock


async def emit_document(messages, document, event_type='chat:message:document', rename=None):
    upsert = AsyncMock()
    with (
        patch.object(socket_main.sio, 'emit', AsyncMock()),
        patch.object(socket_main.Chats, 'update_message_with_lock', locked_update(messages, upsert)),
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

    async def update_message_with_lock(chat_id, build, touch=True):
        snapshot = copy.deepcopy(stored)
        # Lets the other update read the same state before this one writes, like two requests hitting the DB
        await asyncio.sleep(0)
        message_id, message = build(snapshot)
        stored[message_id] = {**stored.get(message_id, {}), **message}

    with patch.object(socket_main.Chats, 'update_message_with_lock', update_message_with_lock):
        await asyncio.gather(
            socket_main.upsert_document_card('chat-1', 'message-1', {'file_id': 'file-1', 'drive_id': 'd-1'}, True),
            socket_main.upsert_document_card('chat-1', 'message-1', {'file_id': 'file-2', 'drive_id': 'd-2'}, True),
        )

    assert [d.get('drive_id') for d in stored['message-1']['documents']] == ['d-1', 'd-2']
    assert 'chat-1' not in socket_main.DOCUMENT_CARD_LOCKS
