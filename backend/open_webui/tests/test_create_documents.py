import asyncio
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import open_webui.tools.built_in as built_in
from open_webui.utils import document_builders
from open_webui.utils.rate_limit import RateLimiter

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


@pytest.fixture(autouse=True)
def fresh_rate_limiter():
    RateLimiter._memory_store.clear()
    with patch.object(built_in, '_document_rate_limiter', RateLimiter(None, limit=built_in.DOCUMENT_MAX_PER_HOUR, window=3600)):
        yield
    RateLimiter._memory_store.clear()


async def fake_build(format, name, content):
    if name == 'Broken':
        raise ValueError('bad content')
    if name == 'Big':
        return b'x' * 2_000_001
    return b'bytes'


async def create(files, upload=None, chat_id='chat-1', emitted=None, already_this_hour=0):
    stored = []

    async def default_upload(request, file, metadata, process, user, generated=False):
        stored.append(
            {
                'filename': file.filename,
                'content_type': file.content_type,
                'metadata': metadata,
                'process': process,
                'generated': generated,
            }
        )
        return SimpleNamespace(id=f'file-{len(stored)}')

    async def emit(event):
        emitted.append(event)

    insert_chat_files = AsyncMock()
    with (
        patch.object(built_in, '_build_document_bytes', fake_build),
        patch('open_webui.routers.files.upload_file_handler', upload or default_upload),
        patch.object(built_in.Chats, 'insert_chat_files', insert_chat_files),
    ):
        for _ in range(already_this_hour):
            built_in._document_rate_limiter.is_limited(USER['id'])
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
    # a missing metadata falls back to the route's Form(None) default, which upload_file_handler can't read
    assert all(s['metadata'] == {} for s in stored)
    # the server marks generated files itself, so cleanup can tell them apart from uploads
    assert all(s['generated'] is True for s in stored)
    insert_chat_files.assert_awaited_once_with(
        chat_id='chat-1', message_id='message-1', file_ids=['file-1', 'file-2'], user_id='user-1'
    )
    assert [e['type'] for e in emitted] == ['chat:message:document'] * 2
    assert emitted[0]['data'] == {'file_id': 'file-1', 'name': 'Report', 'format': 'docx'}


@pytest.mark.asyncio
async def test_one_failed_build_or_store_does_not_fail_the_rest():
    stored = []

    async def upload(request, file, metadata, process, user, generated=False):
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
    assert [f['name'] for f in result['failed']] == ['Broken.xlsx', 'Huge.pdf', 'Notes.odt']
    assert result['failed'][1]['error'] == "Couldn't save this document - File too large"
    assert 'which documents were not created' in result['note']


@pytest.mark.asyncio
async def test_wrongly_typed_fields_fail_only_their_own_document():
    emitted = []
    result, stored, _ = await create(
        [
            {'name': 'Report', 'format': 'docx', 'content': '# Hi'},
            {'name': 'A', 'format': 5, 'content': 'x'},
            {'name': 123, 'format': 'pdf', 'content': 'x'},
            {'name': 'B', 'format': 'pdf', 'content': ['x']},
            'not a document',
        ],
        emitted=emitted,
    )

    assert result['status'] == 'partial'
    assert [s['filename'] for s in stored] == ['Report.docx']
    assert len(emitted) == 1
    assert len(result['failed']) == 4


@pytest.mark.asyncio
async def test_a_document_over_the_size_limit_fails_on_its_own():
    with patch.object(built_in, 'DOCUMENT_MAX_BYTES', 2_000_000):
        result, stored, _ = await create(
            [{'name': 'Small', 'format': 'pdf', 'content': 'x'}, {'name': 'Big', 'format': 'pdf', 'content': 'y'}]
        )

    assert [s['filename'] for s in stored] == ['Small.pdf']
    assert result['status'] == 'partial'
    assert result['failed'] == [
        {'name': 'Big.pdf', 'error': 'The file is larger than 2 MB - split the content into smaller documents.'}
    ]


@pytest.mark.asyncio
async def test_documents_over_the_per_call_limit_fail_and_the_rest_are_created():
    with patch.object(built_in, 'DOCUMENT_MAX_PER_CALL', 2):
        result, stored, _ = await create([{'name': f'Doc {i}', 'format': 'pdf', 'content': 'x'} for i in range(4)])

    assert [s['filename'] for s in stored] == ['Doc 0.pdf', 'Doc 1.pdf']
    assert result['status'] == 'partial'
    assert [f['name'] for f in result['failed']] == ['Doc 2.pdf', 'Doc 3.pdf']
    assert result['failed'][0]['error'] == 'Only 2 documents can be created per call - create this one in a new call.'


@pytest.mark.asyncio
async def test_documents_over_the_hourly_limit_fail_and_the_rest_are_created():
    with (
        patch.object(built_in, 'DOCUMENT_MAX_PER_HOUR', 5),
        patch.object(built_in, '_document_rate_limiter', RateLimiter(None, limit=5, window=3600)),
    ):
        result, stored, _ = await create(
            [{'name': f'Doc {i}', 'format': 'pdf', 'content': 'x'} for i in range(4)], already_this_hour=3
        )

    assert [s['filename'] for s in stored] == ['Doc 0.pdf', 'Doc 1.pdf']
    assert [f['name'] for f in result['failed']] == ['Doc 2.pdf', 'Doc 3.pdf']
    assert result['failed'][0]['error'] == 'The limit of 5 generated documents per hour is reached - try again later.'


@pytest.mark.asyncio
async def test_documents_left_after_the_hour_is_used_up_share_one_reason_and_one_sentence():
    with (
        patch.object(built_in, 'DOCUMENT_MAX_PER_CALL', 3),
        patch.object(built_in, 'DOCUMENT_MAX_PER_HOUR', 4),
        patch.object(built_in, '_document_rate_limiter', RateLimiter(None, limit=4, window=3600)),
    ):
        result, stored, _ = await create([{'name': f'Test {i}', 'format': 'pdf', 'content': 'x'} for i in range(10)], already_this_hour=2)

    hour_error = 'The limit of 4 generated documents per hour is reached - try again later.'
    assert len(stored) == 2
    # Documents 3 and up are over the per-call limit too, but the hour being used up is the reason they all share
    assert {f['error'] for f in result['failed']} == {hour_error}
    assert result['message'] == f'Created 2 of 10 documents. Not created - 8 documents left out: {hour_error}'
    assert built_in.DOCUMENT_LIMIT_REACHED_NOTE in result['note']


def test_documents_left_out_by_the_hourly_limit_are_never_named_even_when_only_one_or_two():
    error = 'The limit of 40 generated documents per hour is reached - try again later.'
    failed = [{'name': 'Test 19.docx', 'error': error, 'limit_reached': True}]
    assert built_in._documents_failures_text(failed) == f'1 document left out: {error}'

    failed.append({'name': 'Test 20.docx', 'error': error, 'limit_reached': True})
    failed.append({'name': 'Broken.xlsx', 'error': "Couldn't build."})
    assert built_in._documents_failures_text(failed) == f"Broken.xlsx: Couldn't build. 2 documents left out: {error}"


def test_documents_failing_for_the_same_reason_are_named_only_when_there_are_a_few():
    failed = [{'name': f'D{i}.pdf', 'error': 'Empty content.'} for i in range(3)]
    assert built_in._documents_failures_text(failed) == '3 documents (D0.pdf, D1.pdf, D2.pdf): Empty content.'

    failed += [{'name': 'D3.pdf', 'error': 'Empty content.'}, {'name': 'X.pdf', 'error': "Couldn't save."}]
    assert built_in._documents_failures_text(failed) == "4 documents: Empty content. X.pdf: Couldn't save."


@pytest.mark.asyncio
async def test_the_hourly_limit_does_not_reset_when_the_documents_are_deleted():
    with (
        patch.object(built_in, 'DOCUMENT_MAX_PER_HOUR', 2),
        patch.object(built_in, '_document_rate_limiter', RateLimiter(None, limit=2, window=3600)),
    ):
        first, _, _ = await create([{'name': f'A{i}', 'format': 'pdf', 'content': 'x'} for i in range(2)])
        # The files are gone from storage and the database by now, so nothing in them can count
        second, stored, _ = await create([{'name': 'B', 'format': 'pdf', 'content': 'x'}])

    assert first['status'] == 'success'
    assert stored == []
    assert 'per hour is reached' in second['failed'][0]['error']


class FakeBuildProcess:
    pid = 0
    returncode = 0

    async def communicate(self, payload):
        await asyncio.sleep(0.05)
        return b'bytes', b''


@pytest.mark.asyncio
async def test_only_a_few_documents_are_built_at_the_same_time():
    running = {'now': 0, 'peak': 0}

    async def fake_subprocess(*args, **kwargs):
        running['now'] += 1
        running['peak'] = max(running['peak'], running['now'])

        class Process(FakeBuildProcess):
            async def communicate(self, payload):
                await asyncio.sleep(0.05)
                running['now'] -= 1
                return b'bytes', b''

        return Process()

    # A fresh semaphore, since the module's one may be tied to another test's event loop
    with (
        patch.object(built_in, '_document_build_slots', asyncio.Semaphore(built_in.DOCUMENT_BUILD_CONCURRENCY)),
        patch('asyncio.create_subprocess_exec', fake_subprocess),
    ):
        results = await asyncio.gather(*(built_in._build_document_bytes('pdf', f'Doc {i}', 'x') for i in range(8)))

    assert results == [b'bytes'] * 8
    assert running['peak'] == built_in.DOCUMENT_BUILD_CONCURRENCY


@pytest.mark.asyncio
async def test_a_build_runs_in_a_child_process_and_returns_the_file():
    file_bytes = await built_in._build_document_bytes('xlsx', 'Data', 'a,b\n1,2')

    assert file_bytes.startswith(b'PK')


@pytest.mark.asyncio
async def test_a_build_that_runs_too_long_is_killed():
    hang = [sys.executable, '-c', 'import time; time.sleep(60)']
    started = time.monotonic()
    with (
        patch.object(built_in, 'DOCUMENT_BUILD_COMMAND', hang),
        patch.object(built_in, 'DOCUMENT_BUILD_TIMEOUT_SECONDS', 0.5),
        pytest.raises(built_in.DocumentBuildTimeout),
    ):
        await built_in._build_document_bytes('pdf', 'Hangs', 'x')

    assert time.monotonic() - started < 10


@pytest.mark.asyncio
async def test_a_build_that_crashes_reports_what_the_process_said():
    crash = [sys.executable, '-c', 'import sys; sys.stderr.write("fonts missing"); sys.exit(1)']
    with patch.object(built_in, 'DOCUMENT_BUILD_COMMAND', crash), pytest.raises(built_in.DocumentBuildError) as e:
        await built_in._build_document_bytes('pdf', 'Crashes', 'x')

    assert 'fonts missing' in str(e.value)


@pytest.mark.asyncio
async def test_a_timed_out_document_fails_on_its_own_with_a_clear_message():
    async def build(format, name, content):
        if name == 'Hangs':
            raise built_in.DocumentBuildTimeout('too long')
        return b'bytes'

    with patch.object(built_in, '_build_document_bytes', build):
        ok, ok_error = await built_in._build_document_safe({'format': 'pdf', 'name': 'Fine', 'content': 'x'})
        timed_out, error = await built_in._build_document_safe({'format': 'docx', 'name': 'Hangs', 'content': 'x'})

    assert ok == b'bytes' and ok_error is None
    assert timed_out is None
    assert error.startswith(f'Building this docx file took longer than {built_in.DOCUMENT_BUILD_TIMEOUT_SECONDS} seconds')


@pytest.mark.asyncio
async def test_a_document_over_the_content_limit_fails_on_its_own():
    with patch.object(built_in, 'DOCUMENT_MAX_CONTENT_CHARS', 10):
        result, stored, _ = await create(
            [{'name': 'Short', 'format': 'pdf', 'content': 'x' * 10}, {'name': 'Long', 'format': 'pdf', 'content': 'x' * 11}]
        )

    assert [s['filename'] for s in stored] == ['Short.pdf']
    assert result['failed'] == [
        {'name': 'Long.pdf', 'error': 'The content is longer than 10 characters - split it into several documents.'}
    ]


@pytest.mark.asyncio
async def test_a_child_that_builds_a_file_over_the_size_limit_is_reported_as_too_large():
    # xlsx of 5000 rows is far over 1 byte, so the child refuses to hand it over
    with patch.object(built_in, 'DOCUMENT_MAX_BYTES', 1), pytest.raises(built_in.DocumentTooLarge):
        await built_in._build_document_bytes('xlsx', 'Data', 'a,b\n1,2')


@pytest.mark.asyncio
async def test_a_killed_build_leaves_no_temp_directory_behind(tmp_path):
    seen = tmp_path / 'tmp_dir_path'
    script = (
        'import json, sys, time; spec = json.loads(sys.stdin.read()); '
        f'open({str(seen)!r}, "w").write(spec["tmp_dir"]); '
        'open(spec["tmp_dir"] + "/half-written.docx", "w").write("x"); time.sleep(60)'
    )
    with (
        patch.object(built_in, 'DOCUMENT_BUILD_COMMAND', [sys.executable, '-c', script]),
        patch.object(built_in, 'DOCUMENT_BUILD_TIMEOUT_SECONDS', 1),
        pytest.raises(built_in.DocumentBuildTimeout),
    ):
        await built_in._build_document_bytes('docx', 'Hangs', 'x')

    import os

    assert seen.exists()
    assert not os.path.exists(seen.read_text())


@pytest.mark.asyncio
async def test_the_user_is_not_shown_the_childs_traceback():
    async def build(format, name, content):
        raise built_in.DocumentBuildError('The pdf build exited with 1: Traceback (most recent call last): /app/secret.py')

    with patch.object(built_in, '_build_document_bytes', build):
        _, error = await built_in._build_document_safe({'format': 'pdf', 'name': 'T', 'content': 'x'})

    assert error == "Couldn't build this pdf file - content must be markdown text."


@pytest.mark.asyncio
async def test_slashes_in_a_name_are_replaced_so_the_card_and_stored_file_match():
    result, stored, _ = await create([{'name': 'Q1/Q2\\Q3 Report', 'format': 'pdf', 'content': 'x'}])

    assert result['created'][0]['name'] == 'Q1-Q2-Q3 Report'
    assert stored[0]['filename'] == 'Q1-Q2-Q3 Report.pdf'


@pytest.mark.asyncio
async def test_temporary_chat_skips_chat_file_linking():
    result, _, insert_chat_files = await create(
        [{'name': 'Report', 'format': 'docx', 'content': '# Hi'}], chat_id='temporary:chat-1'
    )

    assert result['status'] == 'success'
    insert_chat_files.assert_not_awaited()


async def builtin_tool_names(model):
    from open_webui.utils.tools import get_builtin_tools

    # An admin outside the internal domains skips permission and connector lookups, leaving only model-driven tools
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={}))),
        state=SimpleNamespace(),
    )
    tools = await get_builtin_tools(request, {'__user__': {**USER, 'role': 'admin'}}, model=model)
    return set(tools)


@pytest.mark.asyncio
async def test_every_model_can_create_documents_by_default():
    assert 'create_documents' in await builtin_tool_names({'info': {'meta': {}}})


@pytest.mark.asyncio
async def test_turning_off_the_documents_toggle_removes_create_documents_from_the_model():
    names = await builtin_tool_names({'info': {'meta': {'builtinTools': {'documents': False}}}})

    assert 'create_documents' not in names
    assert 'get_current_timestamp' in names


@pytest.mark.asyncio
@pytest.mark.parametrize('documents_on', [True, False])
async def test_drive_save_documents_is_only_offered_with_create_documents(documents_on):
    import open_webui.utils.tools as tools

    write_access = SimpleNamespace(scopes=f'{tools.GOOGLE_DRIVE_WRITE_SCOPE} email')
    model = {'info': {'meta': {'builtinTools': {'documents': documents_on}}}}
    with (
        patch.object(tools, 'is_internal_email', return_value=True),
        patch.object(tools.ConnectorConnections, 'get_by_user_and_connector', AsyncMock(return_value=write_access)),
    ):
        names = await builtin_tool_names(model)

    # Without create_documents the model has no file_ids to save, other Drive write tools stay
    assert ('drive_save_documents' in names) is documents_on
    assert 'drive_create_files' in names


def test_pdf_builds_with_bold_italic_and_code_with_diacritics():
    content = '# Popis\n\n***Važno*** i **_hitno_**\n\n- `ključ` od ureda\n\n```\nčćžšđ\n```\n'

    assert document_builders._build_pdf_document_bytes('Checklist', content).startswith(b'%PDF')


def test_pdf_keeps_spaces_between_inline_tags_and_drops_newlines_between_blocks():
    from fpdf import FPDF

    written = []
    original = FPDF.write_html

    def capture(self, text, *args, **kwargs):
        written.append(text)
        return original(self, text, *args, **kwargs)

    with patch.object(FPDF, 'write_html', capture):
        document_builders._build_pdf_document_bytes('T', '# T\n\n**Ime:** *Ivan* i [a](x) [b](y)\n\n- prvi\n- drugi\n')

    html = written[0]
    assert '</strong> <em>' in html
    assert '</a> <a' in html
    assert '\n' not in html


def test_pptx_uses_google_slides_page_boxes_and_a_cover_slide():
    import io

    from pptx import Presentation

    content = 'Selidba ureda\nPlan za upravu\n---\nCiljevi\n- prvi\n- drugi'
    prs = Presentation(io.BytesIO(document_builders._build_pptx_document_bytes(content)))

    assert (prs.slide_width, prs.slide_height) == document_builders.PPTX_SLIDE_SIZE
    cover, slide = prs.slides
    assert cover.slide_layout == prs.slide_layouts[0]
    assert [p.text_frame.text for p in cover.placeholders] == ['Selidba ureda', 'Plan za upravu']
    assert slide.placeholders[1].text_frame.text == 'prvi\ndrugi'
    # The content slide inherits Google's boxes from its layout instead of setting its own
    layout_boxes = {
        p.placeholder_format.idx: (p.left, p.top, p.width, p.height) for p in slide.slide_layout.placeholders
    }
    assert layout_boxes[0] == document_builders.PPTX_TITLE_BOX
    assert layout_boxes[1] == document_builders.PPTX_BODY_BOX

    # Google's cover is centered, its content titles are left-aligned and bullets are a filled circle
    from pptx.oxml.ns import qn

    cover_title = cover.slide_layout.placeholders[0]._element
    assert cover_title.find('.//' + qn('a:lvl1pPr')).get('algn') == 'ctr'
    assert cover_title.find('.//' + qn('a:bodyPr')).get('anchor') == 'b'
    tx_styles = prs.slide_master._element.find(qn('p:txStyles'))
    assert tx_styles.find(qn('p:titleStyle')).find(qn('a:lvl1pPr')).get('algn') == 'l'
    body_lvl1 = tx_styles.find(qn('p:bodyStyle')).find(qn('a:lvl1pPr'))
    assert body_lvl1.find(qn('a:buChar')).get('char') == '●'
    assert (body_lvl1.get('marL'), body_lvl1.get('indent')) == ('457200', '-342900')


@pytest.mark.parametrize(
    'text, expected',
    [
        ('120', (120, None)),
        ('10,749.20', (10749.2, '#,##0.00')),
        ('12.5%', (0.125, '0.0%')),
        ('007', ('007', None)),
        ('10.749,20', ('10.749,20', None)),
        ('Kutije', ('Kutije', None)),
        ('12345678901234567', ('12345678901234567', None)),
        ('12345678901234.56', ('12345678901234.56', None)),
        ('123456789012345', (123456789012345, None)),
        ("'123456789012345678", ('123456789012345678', None)),
        ("'120", ('120', None)),
        ("''x", ("'x", None)),
    ],
)
def test_xlsx_cells_become_numbers_only_when_they_plainly_are(text, expected):
    assert document_builders._xlsx_cell_value(text) == expected


def test_xlsx_has_arial_a_bold_frozen_header_and_numeric_cells():
    import io

    from openpyxl import load_workbook

    content = 'Stavka,Cijena\nPrijevoz kamionom i dostava na novu lokaciju,"1,200.00"\n'
    ws = load_workbook(io.BytesIO(document_builders._build_xlsx_document_bytes(content))).active

    header, price = ws['A1'], ws['B2']
    assert (header.font.name, header.font.sz, header.font.b) == ('Arial', 10, True)
    assert ws['A2'].font.name == 'Arial' and not ws['A2'].font.b
    assert (price.value, price.number_format) == (1200.0, '#,##0.00')
    assert ws.freeze_panes == 'A2'
    assert ws.title == 'Sheet1'
    assert ws.column_dimensions['A'].width > ws.column_dimensions['B'].width == document_builders.XLSX_MIN_COLUMN_WIDTH


def test_reference_doc_is_built_once_by_parallel_docx_builds_and_a_failure_is_retried(monkeypatch):
    import threading
    import time

    builds = []

    def build():
        builds.append(threading.current_thread().name)
        time.sleep(0.05)
        return None if len(builds) == 1 else 'reference.docx'

    monkeypatch.setattr(document_builders, '_build_pandoc_reference_doc', build)
    monkeypatch.setattr(document_builders, '_reference_doc', None)

    # The first build fails, so the next call tries again instead of keeping pandoc's defaults for good
    assert document_builders._get_pandoc_reference_doc() is None
    threads = [threading.Thread(target=document_builders._get_pandoc_reference_doc) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(builds) == 2
    assert document_builders._get_pandoc_reference_doc() == 'reference.docx'


@pytest.mark.parametrize('content', ['---', '\n---\n  \n---'])
def test_pptx_with_no_slides_fails_instead_of_saving_an_empty_deck(content):
    with pytest.raises(ValueError):
        document_builders._build_pptx_document_bytes(content)
