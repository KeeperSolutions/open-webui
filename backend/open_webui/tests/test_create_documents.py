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
        stored.append(
            {'filename': file.filename, 'content_type': file.content_type, 'metadata': metadata, 'process': process}
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

    assert built_in._build_pdf_document_bytes('Checklist', content).startswith(b'%PDF')


def test_pdf_keeps_spaces_between_inline_tags_and_drops_newlines_between_blocks():
    from fpdf import FPDF

    written = []
    original = FPDF.write_html

    def capture(self, text, *args, **kwargs):
        written.append(text)
        return original(self, text, *args, **kwargs)

    with patch.object(FPDF, 'write_html', capture):
        built_in._build_pdf_document_bytes('T', '# T\n\n**Ime:** *Ivan* i [a](x) [b](y)\n\n- prvi\n- drugi\n')

    html = written[0]
    assert '</strong> <em>' in html
    assert '</a> <a' in html
    assert '\n' not in html


def test_pptx_uses_google_slides_page_boxes_and_a_cover_slide():
    import io

    from pptx import Presentation

    content = 'Selidba ureda\nPlan za upravu\n---\nCiljevi\n- prvi\n- drugi'
    prs = Presentation(io.BytesIO(built_in._build_pptx_document_bytes(content)))

    assert (prs.slide_width, prs.slide_height) == built_in.PPTX_SLIDE_SIZE
    cover, slide = prs.slides
    assert cover.slide_layout == prs.slide_layouts[0]
    assert [p.text_frame.text for p in cover.placeholders] == ['Selidba ureda', 'Plan za upravu']
    assert slide.placeholders[1].text_frame.text == 'prvi\ndrugi'
    # The content slide inherits Google's boxes from its layout instead of setting its own
    layout_boxes = {
        p.placeholder_format.idx: (p.left, p.top, p.width, p.height) for p in slide.slide_layout.placeholders
    }
    assert layout_boxes[0] == built_in.PPTX_TITLE_BOX
    assert layout_boxes[1] == built_in.PPTX_BODY_BOX

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
    ],
)
def test_xlsx_cells_become_numbers_only_when_they_plainly_are(text, expected):
    assert built_in._xlsx_cell_value(text) == expected


def test_xlsx_has_arial_a_bold_frozen_header_and_numeric_cells():
    import io

    from openpyxl import load_workbook

    content = 'Stavka,Cijena\nPrijevoz kamionom i dostava na novu lokaciju,"1,200.00"\n'
    ws = load_workbook(io.BytesIO(built_in._build_xlsx_document_bytes(content))).active

    header, price = ws['A1'], ws['B2']
    assert (header.font.name, header.font.sz, header.font.b) == ('Arial', 10, True)
    assert ws['A2'].font.name == 'Arial' and not ws['A2'].font.b
    assert (price.value, price.number_format) == (1200.0, '#,##0.00')
    assert ws.freeze_panes == 'A2'
    assert ws.title == 'Sheet1'
    assert ws.column_dimensions['A'].width > ws.column_dimensions['B'].width == built_in.XLSX_MIN_COLUMN_WIDTH


def test_reference_doc_is_built_once_by_parallel_docx_builds_and_a_failure_is_retried(monkeypatch):
    import threading
    import time

    builds = []

    def build():
        builds.append(threading.current_thread().name)
        time.sleep(0.05)
        return None if len(builds) == 1 else 'reference.docx'

    monkeypatch.setattr(built_in, '_build_pandoc_reference_doc', build)
    monkeypatch.setattr(built_in, '_reference_doc', None)

    # The first build fails, so the next call tries again instead of keeping pandoc's defaults for good
    assert built_in._get_pandoc_reference_doc() is None
    threads = [threading.Thread(target=built_in._get_pandoc_reference_doc) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(builds) == 2
    assert built_in._get_pandoc_reference_doc() == 'reference.docx'
