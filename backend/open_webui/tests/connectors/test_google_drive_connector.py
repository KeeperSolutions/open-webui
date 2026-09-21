import json
from unittest.mock import AsyncMock, patch

import pytest

import open_webui.tools.built_in as drive

USER = {'id': 'test-user-id'}


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers = {}

    def json(self):
        return self._payload


class FakeClientBase:
    """Every tool wraps its Drive calls in `async with httpx.AsyncClient() as client`,
    sometimes more than once per call - this makes every FakeClient usable there."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.fixture(autouse=True)
def mock_access_token():
    with patch(
        'open_webui.routers.connectors.get_valid_access_token', new=AsyncMock(return_value='fake-token')
    ):
        yield


async def confirm(payload):
    return True


async def decline(payload):
    return False


# ---------------------------------------------------------------------------
# Folder resolution (shared by every write tool)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize('alias', ['My Drive', 'root', 'Root', '  my drive  ', 'Drive'])
async def test_root_folder_aliases_resolve_without_a_network_call(alias):
    folder_id, error = await drive._drive_resolve_folder_id(None, None, alias)
    assert folder_id == 'root'
    assert error is None


@pytest.mark.asyncio
async def test_resolve_folder_id_not_found():
    class FakeClient(FakeClientBase):
        async def get(self, url, headers=None, params=None):
            return FakeResponse(200, {'files': []})

    folder_id, error = await drive._drive_resolve_folder_id(FakeClient(), {}, 'Nonexistent')
    assert folder_id is None
    assert 'No folder named' in error


@pytest.mark.asyncio
async def test_resolve_folder_id_ambiguous():
    class FakeClient(FakeClientBase):
        async def get(self, url, headers=None, params=None):
            return FakeResponse(200, {'files': [{'id': 'f1'}, {'id': 'f2'}]})

    folder_id, error = await drive._drive_resolve_folder_id(FakeClient(), {}, 'Reports')
    assert folder_id is None
    assert 'ask the user which one' in error


# ---------------------------------------------------------------------------
# _drive_call_raw / _drive_call (shared request+status-check helper)
# ---------------------------------------------------------------------------


class TestDriveCallHelpers:
    @pytest.mark.asyncio
    async def test_call_raw_returns_data_on_success(self):
        class FakeClient(FakeClientBase):
            async def request(self, method, url, headers=None, **kwargs):
                return FakeResponse(200, {'id': 'file1'})

        data, error = await drive._drive_call_raw(
            FakeClient(), 'PATCH', 'https://example.com', {}, 'label', 'fallback'
        )
        assert data == {'id': 'file1'}
        assert error is None

    @pytest.mark.asyncio
    async def test_call_raw_returns_plain_string_error_on_failure(self):
        class FakeClient(FakeClientBase):
            async def request(self, method, url, headers=None, **kwargs):
                return FakeResponse(403, {'error': {'errors': [{'reason': 'insufficientFilePermissions'}]}})

        data, error = await drive._drive_call_raw(
            FakeClient(), 'PATCH', 'https://example.com', {}, 'label', 'fallback message'
        )
        assert data is None
        assert error == "You don't have permission to access this file or Drive."

    @pytest.mark.asyncio
    async def test_call_wraps_the_same_error_as_json(self):
        class FakeClient(FakeClientBase):
            async def request(self, method, url, headers=None, **kwargs):
                return FakeResponse(500, {})

        data, error = await drive._drive_call(FakeClient(), 'PATCH', 'https://example.com', {}, 'label', 'fallback')
        assert data is None
        assert json.loads(error) == {'error': 'fallback'}


# ---------------------------------------------------------------------------
# drive_create_files (single or batch)
# ---------------------------------------------------------------------------


class TestDriveCreateFiles:
    @pytest.mark.asyncio
    async def test_creates_in_named_folder_with_parents_set(self):
        class FakeClient(FakeClientBase):
            def __init__(self):
                self.create_calls = []

            async def get(self, url, headers=None, params=None):
                if "mimeType = 'application/vnd.google-apps.folder'" in params['q']:
                    return FakeResponse(200, {'files': [{'id': 'folder-abc', 'name': 'Reports'}]})
                return FakeResponse(200, {'files': []})

            async def request(self, method, url, headers=None, **kwargs):
                self.create_calls.append(kwargs)
                if 'json' not in kwargs:
                    return FakeResponse(200, {})  # the follow-up media upload, response unused
                body = kwargs['json']
                return FakeResponse(
                    200, {'id': 'new123', 'name': body['name'], 'mimeType': body['mimeType'], 'webViewLink': 'x'}
                )

        client = FakeClient()
        with patch('httpx.AsyncClient', return_value=client):
            result = json.loads(
                await drive.drive_create_files(
                    files=[{'name': 'Test File', 'content': 'hello'}], folder='Reports',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['status'] == 'success'
        assert client.create_calls[0]['json']['parents'] == ['folder-abc']

    @pytest.mark.asyncio
    async def test_missing_folder_errors_without_showing_confirmation(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'files': []})

        confirmation_shown = False

        async def confirm_and_flag(payload):
            nonlocal confirmation_shown
            confirmation_shown = True
            return True

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_create_files(
                    files=[{'name': 'Test File'}], folder='Ghost Folder',
                    __user__=USER, __event_call__=confirm_and_flag, __event_emitter__=None,
                )
            )

        assert 'error' in result
        assert confirmation_shown is False

    @pytest.mark.asyncio
    async def test_declined_confirmation_is_reported_as_cancelled_not_an_error(self):
        with patch('httpx.AsyncClient'):
            result = json.loads(
                await drive.drive_create_files(
                    files=[{'name': 'Test File'}], __user__=USER, __event_call__=decline, __event_emitter__=None,
                )
            )

        assert result['status'] == 'cancelled'

    @pytest.mark.asyncio
    async def test_batch_create_single_confirmation(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'files': []})

            async def request(self, method, url, headers=None, **kwargs):
                if 'json' not in kwargs:
                    return FakeResponse(200, {})
                body = kwargs['json']
                return FakeResponse(200, {'id': body['name'], 'name': body['name'], 'mimeType': body['mimeType'], 'webViewLink': 'x'})

        seen_confirmation_data = {}

        async def confirm_and_capture(payload):
            seen_confirmation_data.update(payload['data'])
            return True

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_create_files(
                    files=[{'name': 'One.txt'}, {'name': 'Two.txt'}],
                    __user__=USER, __event_call__=confirm_and_capture, __event_emitter__=None,
                )
            )

        assert result['status'] == 'success'
        assert {c['name'] for c in result['created']} == {'One.txt', 'Two.txt'}
        assert seen_confirmation_data['title'] == 'Create 2 files?'


# ---------------------------------------------------------------------------
# drive_create_folders (parallel siblings and/or nested in one call)
# ---------------------------------------------------------------------------


class TestDriveCreateFolders:
    @pytest.mark.asyncio
    async def test_creates_a_single_folder(self):
        class FakeClient(FakeClientBase):
            async def request(self, method, url, headers=None, **kwargs):
                body = kwargs['json']
                assert body['mimeType'] == 'application/vnd.google-apps.folder'
                return FakeResponse(200, {'id': 'folder1', 'name': body['name'], 'mimeType': body['mimeType']})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_create_folders(
                    folders=[{'name': 'Reports'}], __user__=USER, __event_call__=confirm
                )
            )

        assert result['status'] == 'success'
        assert result['created'] == [{'id': 'folder1', 'name': 'Reports'}]

    @pytest.mark.asyncio
    async def test_creates_nested_folders_in_one_call(self):
        created_bodies = []

        class FakeClient(FakeClientBase):
            async def request(self, method, url, headers=None, **kwargs):
                body = kwargs['json']
                created_bodies.append(body)
                return FakeResponse(200, {'id': f'id-{body["name"]}', 'name': body['name'], 'mimeType': body['mimeType']})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_create_folders(
                    folders=[{'name': '2024'}, {'name': 'Q1', 'parent': '2024'}],
                    __user__=USER, __event_call__=confirm,
                )
            )

        assert result['status'] == 'success'
        assert {c['name'] for c in result['created']} == {'2024', 'Q1'}
        # "2024" must be created (and its real id known) before "Q1" is asked to nest inside it
        parent_call, child_call = created_bodies
        assert 'parents' not in parent_call
        assert child_call['parents'] == ['id-2024']

    @pytest.mark.asyncio
    async def test_independent_folders_single_confirmation(self):
        seen_confirmation_data = {}

        async def confirm_and_capture(payload):
            seen_confirmation_data.update(payload['data'])
            return True

        class FakeClient(FakeClientBase):
            async def request(self, method, url, headers=None, **kwargs):
                body = kwargs['json']
                return FakeResponse(200, {'id': f'id-{body["name"]}', 'name': body['name'], 'mimeType': body['mimeType']})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_create_folders(
                    folders=[{'name': 'Alpha'}, {'name': 'Beta'}],
                    __user__=USER, __event_call__=confirm_and_capture,
                )
            )

        assert result['status'] == 'success'
        assert {c['name'] for c in result['created']} == {'Alpha', 'Beta'}
        assert seen_confirmation_data['title'] == 'Create 2 folders?'

    @pytest.mark.asyncio
    async def test_circular_parent_reference_errors(self):
        confirmation_shown = False

        async def confirm_and_flag(payload):
            nonlocal confirmation_shown
            confirmation_shown = True
            return True

        result = json.loads(
            await drive.drive_create_folders(
                folders=[{'name': 'A', 'parent': 'B'}, {'name': 'B', 'parent': 'A'}],
                __user__=USER, __event_call__=confirm_and_flag,
            )
        )

        assert 'Circular' in result['error']
        assert confirmation_shown is False

    @pytest.mark.asyncio
    async def test_failed_parent_cascades_to_its_children(self):
        class FakeClient(FakeClientBase):
            async def request(self, method, url, headers=None, **kwargs):
                body = kwargs['json']
                if body['name'] == 'Parent':
                    return FakeResponse(403, {'error': {'message': 'insufficient permissions'}})
                return FakeResponse(200, {'id': f'id-{body["name"]}', 'name': body['name'], 'mimeType': body['mimeType']})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_create_folders(
                    folders=[{'name': 'Parent'}, {'name': 'Child', 'parent': 'Parent'}],
                    __user__=USER, __event_call__=confirm,
                )
            )

        assert result['status'] == 'error'
        assert result['created'] == []
        assert {f['name'] for f in result['failed']} == {'Parent', 'Child'}


# ---------------------------------------------------------------------------
# drive_copy_files (single or batch)
# ---------------------------------------------------------------------------


class TestDriveCopyFiles:
    @pytest.mark.asyncio
    async def test_copy_into_a_different_folder_sets_parents(self):
        class FakeClient(FakeClientBase):
            def __init__(self):
                self.copy_calls = []

            async def get(self, url, headers=None, params=None):
                if url == f'{drive.GOOGLE_DRIVE_FILES_URL}/src123':
                    return FakeResponse(200, {'name': 'Original.txt'})
                if "mimeType = 'application/vnd.google-apps.folder'" in params.get('q', ''):
                    return FakeResponse(200, {'files': [{'id': 'folder-xyz', 'name': 'Archive'}]})
                return FakeResponse(200, {'files': []})

            async def request(self, method, url, headers=None, **kwargs):
                self.copy_calls.append(kwargs)
                return FakeResponse(
                    200, {'id': 'copy123', 'name': 'Original.txt', 'mimeType': 'text/plain', 'webViewLink': 'x'}
                )

        client = FakeClient()
        with patch('httpx.AsyncClient', return_value=client):
            result = json.loads(
                await drive.drive_copy_files(
                    file_ids=['src123'], folder='Archive',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['status'] == 'success'
        assert client.copy_calls[0]['json']['parents'] == ['folder-xyz']
        assert client.copy_calls[0]['json']['name'] == 'Copy of Original.txt'

    @pytest.mark.asyncio
    async def test_copy_gets_explicit_name(self):
        # Drive API v3 keeps the exact source name on a copy unless a name is set explicitly -
        # unlike the Drive web UI, which prefixes "Copy of" itself. Regression test for that gap.
        copy_calls = []

        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'name': 'Report.docx'})

            async def request(self, method, url, headers=None, **kwargs):
                copy_calls.append(kwargs)
                return FakeResponse(200, {'id': 'copy1', 'name': kwargs['json']['name'], 'mimeType': 'text/plain'})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_copy_files(
                    file_ids=['file1'], __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert copy_calls[0]['json']['name'] == 'Copy of Report.docx'
        assert result['copied'][0]['name'] == 'Copy of Report.docx'

    @pytest.mark.asyncio
    async def test_copying_the_same_file_twice_keeps_the_same_copy_name(self):
        # Drive allows duplicate names, so no dedup check is needed - matches Drive's own web UI,
        # which also just makes two files both named "Copy of X" if you copy the same file twice.
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'name': 'Report.docx'})

            async def request(self, method, url, headers=None, **kwargs):
                return FakeResponse(200, {'id': 'copy2', 'name': kwargs['json']['name'], 'mimeType': 'text/plain'})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_copy_files(
                    file_ids=['file1'], __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['copied'][0]['name'] == 'Copy of Report.docx'

    @pytest.mark.asyncio
    async def test_batch_copy_single_confirmation(self):
        names = {'file1': 'One.txt', 'file2': 'Two.txt'}

        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                file_id = url.rsplit('/', 1)[-1]
                return FakeResponse(200, {'name': names[file_id]})

            async def request(self, method, url, headers=None, **kwargs):
                file_id = url.split('/')[-2]  # .../<file_id>/copy
                return FakeResponse(200, {'id': f'copy-{file_id}', 'name': kwargs['json']['name'], 'mimeType': 'text/plain'})

        seen_confirmation_data = {}

        async def confirm_and_capture(payload):
            seen_confirmation_data.update(payload['data'])
            return True

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_copy_files(
                    file_ids=list(names), __user__=USER, __event_call__=confirm_and_capture, __event_emitter__=None,
                )
            )

        assert result['status'] == 'success'
        assert {c['id'] for c in result['copied']} == {'copy-file1', 'copy-file2'}
        assert seen_confirmation_data['title'] == 'Copy 2 files?'


# ---------------------------------------------------------------------------
# drive_save_edited_copy
# ---------------------------------------------------------------------------


class TestDriveSaveEditedCopy:
    @pytest.mark.asyncio
    async def test_defaults_to_the_original_files_folder_not_drive_root(self):
        class FakeClient(FakeClientBase):
            def __init__(self):
                self.create_calls = []

            async def get(self, url, headers=None, params=None):
                if url == f'{drive.GOOGLE_DRIVE_FILES_URL}/src789':
                    return FakeResponse(200, {'name': 'Plain Notes', 'mimeType': 'text/plain', 'parents': ['orig-parent']})
                return FakeResponse(200, {'files': []})

            async def request(self, method, url, headers=None, **kwargs):
                self.create_calls.append(kwargs)
                if 'json' not in kwargs:
                    return FakeResponse(200, {})  # the follow-up media upload, response unused
                body = kwargs['json']
                return FakeResponse(
                    200, {'id': 'edited123', 'name': body['name'], 'mimeType': body['mimeType'], 'webViewLink': 'x'}
                )

        client = FakeClient()
        with patch('httpx.AsyncClient', return_value=client):
            result = json.loads(
                await drive.drive_save_edited_copy(
                    file_id='src789', content='updated text',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['status'] == 'success'
        assert result['name'] == 'Edit of Plain Notes'
        assert client.create_calls[0]['json']['parents'] == ['orig-parent']

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'model_guessed_name',
        ['hubgate notes', 'edit of hubgate notes', 'Edit of Hubgate notes', 'EDIT OF HUBGATE NOTES'],
    )
    async def test_discards_a_model_echoed_name_and_rebuilds_it_with_the_real_casing(self, model_guessed_name):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                if url == f'{drive.GOOGLE_DRIVE_FILES_URL}/src1':
                    return FakeResponse(200, {'name': 'Hubgate Notes', 'mimeType': 'text/plain', 'parents': []})
                return FakeResponse(200, {'files': []})

            async def request(self, method, url, headers=None, **kwargs):
                if 'json' not in kwargs:
                    return FakeResponse(200, {})  # the follow-up media upload, response unused
                body = kwargs['json']
                return FakeResponse(
                    200, {'id': 'x', 'name': body['name'], 'mimeType': body['mimeType'], 'webViewLink': 'x'}
                )

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_save_edited_copy(
                    file_id='src1', content='x', name=model_guessed_name,
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['name'] == 'Edit of Hubgate Notes'


# ---------------------------------------------------------------------------
# drive_move_files (single or batch)
# ---------------------------------------------------------------------------


class TestDriveMoveFiles:
    @pytest.mark.asyncio
    async def test_move_sends_add_and_remove_parents(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                if url == f'{drive.GOOGLE_DRIVE_FILES_URL}/file1':
                    return FakeResponse(200, {'name': 'Report.docx', 'parents': ['old-folder']})
                return FakeResponse(200, {'files': [{'id': 'new-folder', 'name': 'Archive'}]})

            async def request(self, method, url, headers=None, **kwargs):
                assert method == 'PATCH'
                assert kwargs['params']['addParents'] == 'new-folder'
                assert kwargs['params']['removeParents'] == 'old-folder'
                return FakeResponse(
                    200, {'id': 'file1', 'name': 'Report.docx', 'mimeType': 'application/vnd.google-apps.document', 'webViewLink': 'x'}
                )

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_move_files(
                    file_ids=['file1'], folder='Archive',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['status'] == 'success'
        assert result['moved'] == [{'id': 'file1', 'name': 'Report.docx', 'folder': 'Archive'}]

    @pytest.mark.asyncio
    async def test_moving_a_file_already_in_the_target_folder_errors_without_asking(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                if url == f'{drive.GOOGLE_DRIVE_FILES_URL}/file1':
                    return FakeResponse(200, {'name': 'X.txt', 'parents': ['same-folder']})
                return FakeResponse(200, {'files': [{'id': 'same-folder', 'name': 'Archive'}]})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_move_files(
                    file_ids=['file1'], folder='Archive',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['status'] == 'error'
        assert 'already in' in result['failed'][0]['error']

    @pytest.mark.asyncio
    async def test_permission_error_from_google_surfaces_cleanly(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                if url == f'{drive.GOOGLE_DRIVE_FILES_URL}/file2':
                    return FakeResponse(200, {'name': 'SomeoneElses.docx', 'parents': ['old-folder']})
                return FakeResponse(200, {'files': [{'id': 'new-folder', 'name': 'Archive'}]})

            async def request(self, method, url, headers=None, **kwargs):
                return FakeResponse(403, {'error': {'errors': [{'reason': 'insufficientFilePermissions'}]}})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_move_files(
                    file_ids=['file2'], folder='Archive',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['status'] == 'error'
        assert result['failed'][0]['id'] == 'file2'

    @pytest.mark.asyncio
    async def test_batch_move_single_confirmation(self):
        names = {'file1': 'One.txt', 'file2': 'Two.txt'}

        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                file_id = url.rsplit('/', 1)[-1]
                if file_id in names:
                    return FakeResponse(200, {'name': names[file_id], 'parents': ['old-folder']})
                return FakeResponse(200, {'files': [{'id': 'new-folder', 'name': 'Archive'}]})

            async def request(self, method, url, headers=None, **kwargs):
                file_id = url.rsplit('/', 1)[-1]
                return FakeResponse(200, {'id': file_id, 'name': names[file_id], 'mimeType': 'text/plain', 'webViewLink': 'x'})

        seen_confirmation_data = {}

        async def confirm_and_capture(payload):
            seen_confirmation_data.update(payload['data'])
            return True

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_move_files(
                    file_ids=list(names), folder='Archive',
                    __user__=USER, __event_call__=confirm_and_capture, __event_emitter__=None,
                )
            )

        assert result['status'] == 'success'
        assert {m['id'] for m in result['moved']} == {'file1', 'file2'}
        assert seen_confirmation_data['title'] == 'Move 2 files?'


# ---------------------------------------------------------------------------
# drive_delete_files and drive_restore_files (single or batch)
# ---------------------------------------------------------------------------


class TestDriveDeleteAndRestore:
    @pytest.mark.asyncio
    async def test_delete_moves_to_trash_and_never_offers_a_skip_checkbox(self):
        seen_confirmation_data = {}

        async def confirm_and_capture(payload):
            seen_confirmation_data.update(payload['data'])
            return True

        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'name': 'Old Draft.docx'})

            async def request(self, method, url, headers=None, **kwargs):
                assert kwargs['json'] == {'trashed': True}
                return FakeResponse(200, {'id': 'file1', 'name': 'Old Draft.docx'})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_delete_files(
                    file_ids=['file1'], __user__=USER, __event_call__=confirm_and_capture
                )
            )

        assert result['status'] == 'success'
        assert result['deleted'] == [{'id': 'file1', 'name': 'Old Draft.docx'}]
        assert 'allow_remember' not in seen_confirmation_data
        # A single file stays inline, no bullet list needed
        assert seen_confirmation_data['message'] == (
            'Move "Old Draft.docx" to Trash? '
            "You can restore it from Google Drive's Trash within 30 days."
        )

    @pytest.mark.asyncio
    async def test_declined_delete_is_cancelled_not_an_error(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'name': 'Old Draft.docx'})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_delete_files(file_ids=['file1'], __user__=USER, __event_call__=decline)
            )

        assert result['status'] == 'cancelled'

    @pytest.mark.asyncio
    async def test_batch_delete_single_confirmation(self):
        names = {'file1': 'Old Draft.docx', 'file2': 'Notes.txt', 'file3': 'Budget.xlsx'}
        seen_confirmation_data = {}

        async def confirm_and_capture(payload):
            seen_confirmation_data.update(payload['data'])
            return True

        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                file_id = url.rsplit('/', 1)[-1]
                return FakeResponse(200, {'name': names[file_id]})

            async def request(self, method, url, headers=None, **kwargs):
                file_id = url.rsplit('/', 1)[-1]
                assert kwargs['json'] == {'trashed': True}
                return FakeResponse(200, {'id': file_id, 'name': names[file_id]})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_delete_files(
                    file_ids=list(names), __user__=USER, __event_call__=confirm_and_capture
                )
            )

        assert result['status'] == 'success'
        assert {d['id'] for d in result['deleted']} == set(names)
        assert result['failed'] == []
        # One confirmation for the whole batch, not one per file
        assert seen_confirmation_data['title'] == 'Move 3 files to Trash?'
        # Names render as bulleted, indented lines, not one long comma-separated line
        message = seen_confirmation_data['message']
        assert '• "Old Draft.docx"' in message
        assert '• "Notes.txt"' in message
        assert '• "Budget.xlsx"' in message
        assert "You can restore them from Google Drive's Trash within 30 days." in message

    @pytest.mark.asyncio
    async def test_batch_delete_partial_failure(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                file_id = url.rsplit('/', 1)[-1]
                if file_id == 'file1':
                    return FakeResponse(200, {'name': 'Old Draft.docx'})
                return FakeResponse(403, {'error': {'message': 'The user does not have sufficient permissions'}})

            async def request(self, method, url, headers=None, **kwargs):
                return FakeResponse(200, {'id': 'file1', 'name': 'Old Draft.docx'})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_delete_files(file_ids=['file1', 'file2'], __user__=USER, __event_call__=confirm)
            )

        assert result['status'] == 'partial'
        assert result['deleted'] == [{'id': 'file1', 'name': 'Old Draft.docx'}]
        assert [f['id'] for f in result['failed']] == ['file2']

    @pytest.mark.asyncio
    async def test_restore_brings_a_trashed_file_back(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'name': 'Trashed.docx', 'trashed': True})

            async def request(self, method, url, headers=None, **kwargs):
                assert kwargs['json'] == {'trashed': False}
                return FakeResponse(
                    200, {'id': 'file3', 'name': 'Trashed.docx', 'mimeType': 'application/vnd.google-apps.document', 'webViewLink': 'x'}
                )

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_restore_files(file_ids=['file3'], __user__=USER, __event_call__=confirm, __event_emitter__=None)
            )

        assert result['status'] == 'success'
        assert result['restored'] == [{'id': 'file3', 'name': 'Trashed.docx'}]

    @pytest.mark.asyncio
    async def test_restoring_a_file_thats_not_trashed_errors_without_asking(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'name': 'NotTrashed.docx', 'trashed': False})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_restore_files(file_ids=['file4'], __user__=USER, __event_call__=confirm, __event_emitter__=None)
            )

        assert result['status'] == 'error'
        assert 'not in Trash' in result['failed'][0]['error']

    @pytest.mark.asyncio
    async def test_batch_restore_single_confirmation(self):
        names = {'file1': 'One.txt', 'file2': 'Two.txt'}

        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                file_id = url.rsplit('/', 1)[-1]
                return FakeResponse(200, {'name': names[file_id], 'trashed': True})

            async def request(self, method, url, headers=None, **kwargs):
                file_id = url.rsplit('/', 1)[-1]
                assert kwargs['json'] == {'trashed': False}
                return FakeResponse(200, {'id': file_id, 'name': names[file_id], 'mimeType': 'text/plain', 'webViewLink': 'x'})

        seen_confirmation_data = {}

        async def confirm_and_capture(payload):
            seen_confirmation_data.update(payload['data'])
            return True

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_restore_files(
                    file_ids=list(names), __user__=USER, __event_call__=confirm_and_capture, __event_emitter__=None
                )
            )

        assert result['status'] == 'success'
        assert {r['id'] for r in result['restored']} == {'file1', 'file2'}
        assert seen_confirmation_data['title'] == 'Restore 2 files from Trash?'


# ---------------------------------------------------------------------------
# drive_rename_file
# ---------------------------------------------------------------------------


class TestDriveRenameFile:
    @pytest.mark.asyncio
    async def test_renames_a_file(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                if url == f'{drive.GOOGLE_DRIVE_FILES_URL}/file1':
                    return FakeResponse(200, {'name': 'Old Name.docx', 'parents': ['folder1']})
                return FakeResponse(200, {'files': []})

            async def request(self, method, url, headers=None, **kwargs):
                assert kwargs['json'] == {'name': 'New Name.docx'}
                return FakeResponse(
                    200, {'id': 'file1', 'name': 'New Name.docx', 'mimeType': 'application/vnd.google-apps.document', 'webViewLink': 'x'}
                )

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_rename_file(
                    file_id='file1', name='New Name.docx',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert result['name'] == 'New Name.docx'

    @pytest.mark.asyncio
    async def test_renaming_to_the_same_name_errors_without_asking(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'name': 'Same.docx', 'parents': ['folder1']})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_rename_file(
                    file_id='file2', name='Same.docx',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert 'already named' in result['error']

    @pytest.mark.asyncio
    async def test_permission_error_from_google_surfaces_cleanly(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                if url == f'{drive.GOOGLE_DRIVE_FILES_URL}/file3':
                    return FakeResponse(200, {'name': 'Old Name.docx', 'parents': ['folder1']})
                return FakeResponse(200, {'files': []})

            async def request(self, method, url, headers=None, **kwargs):
                return FakeResponse(403, {'error': {'errors': [{'reason': 'insufficientFilePermissions'}]}})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(
                await drive.drive_rename_file(
                    file_id='file3', name='New Name.docx',
                    __user__=USER, __event_call__=confirm, __event_emitter__=None,
                )
            )

        assert 'error' in result


# ---------------------------------------------------------------------------
# drive_search and drive_list_folder
# ---------------------------------------------------------------------------


class TestDriveSearchAndListFolder:
    @pytest.mark.asyncio
    async def test_search_paginates_and_tags_next_page_token(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(
                    200,
                    {
                        'files': [{'id': 'f1', 'name': 'Doc', 'mimeType': 'text/plain', 'webViewLink': 'x'}],
                        'nextPageToken': 'token123',
                    },
                )

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(await drive.drive_search(query='Doc', __user__=USER))

        assert result['results'][0]['name'] == 'Doc'
        assert result['next_page_token'] == 'token123'

    @pytest.mark.asyncio
    async def test_list_folder_defaults_to_my_drive_and_flags_subfolders(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                q = params.get('q', '')
                if "mimeType = 'application/vnd.google-apps.folder'" in q:
                    return FakeResponse(200, {'files': []})
                assert "'root' in parents" in q
                return FakeResponse(
                    200,
                    {
                        'files': [
                            {'id': 'f1', 'name': 'Notes.docx', 'mimeType': 'application/vnd.google-apps.document', 'webViewLink': 'x'},
                            {'id': 'f2', 'name': 'Reports', 'mimeType': 'application/vnd.google-apps.folder', 'webViewLink': 'x'},
                        ]
                    },
                )

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(await drive.drive_list_folder(__user__=USER))

        assert result['results'][0]['is_folder'] is False
        assert result['results'][1]['is_folder'] is True

    @pytest.mark.asyncio
    async def test_list_folder_reports_a_missing_folder(self):
        class FakeClient(FakeClientBase):
            async def get(self, url, headers=None, params=None):
                return FakeResponse(200, {'files': []})

        with patch('httpx.AsyncClient', return_value=FakeClient()):
            result = json.loads(await drive.drive_list_folder(folder='Nonexistent', __user__=USER))

        assert 'No folder named' in result['error']
