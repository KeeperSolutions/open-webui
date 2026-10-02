import time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models.automations import AutomationRun
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, ChatFile, Chats
from open_webui.models.files import File
from open_webui.models.shared_chats import SharedChat

NOW = int(time.time())


def chat_row(chat_id, user_id='user-1', folder_id=None):
    return Chat(
        id=chat_id,
        user_id=user_id,
        title=chat_id,
        chat={},
        created_at=NOW,
        updated_at=NOW,
        archived=False,
        pinned=False,
        meta={},
        folder_id=folder_id,
    )


def file_row(file_id, generated):
    meta = {'name': file_id, 'data': {}, **({'generated': True} if generated else {})}
    return File(id=file_id, user_id='user-1', filename=file_id, path=f'/store/{file_id}', created_at=NOW, updated_at=NOW, meta=meta)


def link(chat_id, file_id):
    return ChatFile(
        id=f'{chat_id}-{file_id}', user_id='user-1', chat_id=chat_id, file_id=file_id, created_at=NOW, updated_at=NOW
    )


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        for table in (Chat, ChatMessage, ChatFile, File, AutomationRun, SharedChat):
            await conn.run_sync(table.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                chat_row('chat-1', folder_id='folder-1'),
                chat_row('chat-2'),
                chat_row('chat-3', user_id='user-2'),
                file_row('generated-1', generated=True),
                file_row('generated-shared', generated=True),
                file_row('uploaded', generated=False),
            ]
        )
        await session.commit()
        session.add_all(
            [
                link('chat-1', 'generated-1'),
                link('chat-1', 'generated-shared'),
                link('chat-1', 'uploaded'),
                link('chat-2', 'generated-shared'),
            ]
        )
        await session.commit()
    with (
        patch('open_webui.internal.db.AsyncSessionLocal', session_factory),
        patch('open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING', False),
    ):
        yield session_factory
    await engine.dispose()


async def remaining_files(factory):
    async with factory() as session:
        return {row[0] for row in (await session.execute(select(File.id))).all()}


@pytest.mark.asyncio
async def test_deleting_a_chat_deletes_its_generated_files_but_not_uploads_or_files_another_chat_links(factory):
    with patch('open_webui.storage.provider.Storage.delete_file') as delete_file:
        await Chats.delete_chat_by_id_and_user_id('chat-1', 'user-1')

    assert await remaining_files(factory) == {'generated-shared', 'uploaded'}
    delete_file.assert_called_once_with('/store/generated-1')


@pytest.mark.asyncio
async def test_a_file_shared_by_a_clone_is_deleted_with_the_last_chat_that_links_it(factory):
    with patch('open_webui.storage.provider.Storage.delete_file') as delete_file:
        await Chats.delete_chat_by_id('chat-1')
        await Chats.delete_chat_by_id('chat-2')

    assert await remaining_files(factory) == {'uploaded'}
    assert sorted(call.args[0] for call in delete_file.call_args_list) == ['/store/generated-1', '/store/generated-shared']


@pytest.mark.asyncio
async def test_deleting_all_chats_of_a_user_or_a_folder_deletes_generated_files_too(factory):
    with patch('open_webui.storage.provider.Storage.delete_file'):
        await Chats.delete_chats_by_user_id_and_folder_id('user-1', 'folder-1')
    # chat-2 still links the shared file
    assert await remaining_files(factory) == {'generated-shared', 'uploaded'}

    with patch('open_webui.storage.provider.Storage.delete_file'):
        await Chats.delete_chats_by_user_id('user-1')
    assert await remaining_files(factory) == {'uploaded'}


@pytest.mark.asyncio
async def test_a_failing_storage_delete_does_not_stop_the_chat_from_being_deleted(factory):
    with patch('open_webui.storage.provider.Storage.delete_file', side_effect=OSError('disk gone')):
        assert await Chats.delete_chat_by_id('chat-1') is True

    assert await Chats.get_chat_by_id('chat-1') is None


@pytest.mark.asyncio
async def test_files_stay_when_the_chat_could_not_be_deleted(factory):
    with (
        patch('open_webui.storage.provider.Storage.delete_file') as delete_file,
        patch('open_webui.models.chats.update', side_effect=RuntimeError('lock timeout')),
    ):
        assert await Chats.delete_chat_by_id('chat-1') is False

    assert await Chats.get_chat_by_id('chat-1') is not None
    assert await remaining_files(factory) == {'generated-1', 'generated-shared', 'uploaded'}
    delete_file.assert_not_called()


@pytest.mark.asyncio
async def test_a_client_cannot_mark_its_own_upload_as_generated():
    import io

    from fastapi import UploadFile

    from open_webui.routers.files import upload_file_handler

    inserted = []

    async def insert_new_file(user_id, form, db=None):
        inserted.append(form)
        return form

    with (
        patch('open_webui.routers.files.Storage.upload_file', return_value=(b'x', '/store/x')),
        patch('open_webui.routers.files.Files.insert_new_file', insert_new_file),
        patch('open_webui.routers.files.Channels'),
    ):
        upload = UploadFile(file=io.BytesIO(b'x'), filename='a.docx', headers={'content-type': 'text/plain'})
        request = type('R', (), {'app': type('A', (), {'state': type('S', (), {'config': type('C', (), {'RAG_FILE_MAX_SIZE': None, 'RAG_ALLOWED_FILE_EXTENSIONS': []})()})()})()})()
        user = type('U', (), {'id': 'user-1', 'email': 'e', 'name': 'n'})()
        await upload_file_handler(request, file=upload, metadata={'generated': True}, process=False, user=user)
        await upload_file_handler(request, file=upload, metadata={}, process=False, user=user, generated=True)

    assert 'generated' not in inserted[0].meta
    assert inserted[1].meta['generated'] is True


@pytest.mark.asyncio
async def test_a_copied_chat_links_the_documents_on_its_messages():
    insert = AsyncMock()
    messages = {
        'm1': {'role': 'user', 'content': 'hi'},
        'm2': {'documents': [{'file_id': 'f1'}, {'drive_id': 'd1'}, {'file_id': 'f2'}]},
        'm3': {'documents': [{'drive_id': 'd2'}]},
    }
    with patch.object(Chats, 'insert_chat_files', insert):
        await Chats.link_message_documents('copy-1', messages, 'user-1')

    insert.assert_awaited_once_with(chat_id='copy-1', message_id='m2', file_ids=['f1', 'f2'], user_id='user-1')
