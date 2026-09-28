from open_webui.tools.built_in import drive_create_documents
from open_webui.utils.tools import get_builtin_tool_spec, parse_docstring


def test_parse_docstring_joins_a_param_description_that_spans_lines():
    docstring = """
    Do something.

    :param files: One or more files, each shaped
        {"name": "...", "format": "pdf"}
    :param folder: Target folder
    :param __user__: Injected, never shown to the model
        so this line is skipped too
    :return: JSON result
        spanning two lines
    """

    assert parse_docstring(docstring) == {
        'files': 'One or more files, each shaped {"name": "...", "format": "pdf"}',
        'folder': 'Target folder',
    }


def test_parse_docstring_stops_a_description_at_a_blank_line():
    docstring = """
    :param query: What to search for

    Trailing prose that isn't part of the param.
    """

    assert parse_docstring(docstring) == {'query': 'What to search for'}


def test_drive_create_documents_spec_names_every_document_field():
    files = get_builtin_tool_spec(drive_create_documents)['parameters']['properties']['files']

    assert files['items']['required'] == ['name', 'format', 'content']
    assert files['items']['properties']['format']['enum'] == ['pdf', 'docx', 'xlsx', 'pptx']
