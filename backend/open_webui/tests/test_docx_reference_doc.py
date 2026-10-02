import io
import re
import zipfile
from unittest.mock import patch

import pytest

from open_webui.utils import document_builders

pypandoc = pytest.importorskip('pypandoc')
try:
    pypandoc.get_pandoc_path()
except OSError:
    pytest.skip('pandoc is not installed', allow_module_level=True)


@pytest.fixture
def reference_doc(tmp_path, monkeypatch):
    monkeypatch.setattr(document_builders, '_reference_doc', None)
    with patch('open_webui.config.CACHE_DIR', tmp_path):
        yield document_builders._get_pandoc_reference_doc()


def read_part(docx_bytes: bytes, name: str) -> str:
    return zipfile.ZipFile(io.BytesIO(docx_bytes)).read(name).decode('utf-8')


def test_reference_doc_has_table_borders_and_arial(reference_doc, tmp_path):
    assert reference_doc == tmp_path / 'pandoc' / 'reference.docx'

    docx_bytes = reference_doc.read_bytes()
    styles = read_part(docx_bytes, 'word/styles.xml')
    theme = read_part(docx_bytes, 'word/theme/theme1.xml')

    assert '<w:insideH w:val="single"' in styles
    assert 'w:fill="F2F2F2"' in styles
    assert 'w:ascii="Arial"' in styles
    assert theme.count('<a:latin typeface="Arial"/>') == 2


def test_reference_doc_is_built_once_per_process(reference_doc):
    with patch('subprocess.run') as run:
        assert document_builders._get_pandoc_reference_doc() == reference_doc
    run.assert_not_called()


def test_docx_build_uses_the_restyled_reference_doc(reference_doc):
    docx_bytes = document_builders._build_docx_document_bytes('# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n')
    styles = read_part(docx_bytes, 'word/styles.xml')

    # pandoc re-serialises styles.xml and reorders attributes, so match the element rather than exact markup
    assert re.search(r'<w:insideH [^>]*w:val="single"', styles)
    assert 'w:fill="F2F2F2"' in styles
    assert '<w:tblStyle w:val="Table" />' in read_part(docx_bytes, 'word/document.xml')


def test_missing_style_block_raises():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('word/styles.xml', '<w:styles></w:styles>')

    with pytest.raises(ValueError, match='docDefaults'):
        document_builders._apply_reference_doc_replacements(buf.getvalue())


def test_docx_is_a4_with_google_docs_headings(reference_doc):
    docx_bytes = document_builders._build_docx_document_bytes('# Naslov\n\n### Podnaslov\n\nTekst.\n')
    document = read_part(docx_bytes, 'word/document.xml')
    styles = read_part(docx_bytes, 'word/styles.xml')

    assert re.search(r'<w:pgSz [^>]*w:h="16838"', document) and re.search(r'<w:pgSz [^>]*w:w="11906"', document)
    heading3 = re.search(r'<w:style [^>]*w:styleId="Heading3".*?</w:style>', styles, re.S).group(0)
    assert re.search(r'<w:color [^>]*w:val="434343"', heading3)
    assert '0F4761' not in re.search(r'<w:style [^>]*w:styleId="Heading1".*?</w:style>', styles, re.S).group(0)


def test_a_docx_build_told_to_skip_the_reference_doc_does_not_build_one(monkeypatch):
    def fail():
        raise AssertionError('the reference doc must not be built in the child')

    monkeypatch.setattr(document_builders, '_get_pandoc_reference_doc', fail)
    monkeypatch.setattr(document_builders, '_skip_reference_doc', True)

    assert document_builders._build_docx_document_bytes('# Title').startswith(b'PK')


def test_the_docx_temp_file_goes_in_the_directory_the_parent_gave(tmp_path, monkeypatch):
    monkeypatch.setattr(document_builders, '_skip_reference_doc', True)
    monkeypatch.setitem(document_builders._paths, 'tmp_dir', str(tmp_path))
    used = []
    real_convert = pypandoc.convert_text

    def spy(*args, **kwargs):
        used.append(kwargs['outputfile'])
        return real_convert(*args, **kwargs)

    monkeypatch.setattr(pypandoc, 'convert_text', spy)
    document_builders._build_docx_document_bytes('# Title')

    assert used and used[0].startswith(str(tmp_path))
