import sys
from pathlib import Path
from unittest.mock import patch
import docx


THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.importers.office_importer import OfficeImporter


def test_office_importer_supports_docx():
    importer = OfficeImporter()
    assert importer.supports('file.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')


def test_office_importer_supports_doc():
    importer = OfficeImporter()
    assert importer.supports('file.doc', 'application/msword')


def test_office_importer_supports_xlsx():
    importer = OfficeImporter()
    assert importer.supports('file.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def test_office_importer_does_not_support_txt():
    importer = OfficeImporter()
    assert not importer.supports('file.txt', 'text/plain')


@patch('app.importers.office_importer.textract.process')
def test_office_importer_read_preview_non_docx(mock_process):
    mock_process.return_value = b'Hello Office World'
    importer = OfficeImporter()
    preview = importer.read_preview('file.xls', 10)
    assert preview == 'Hello Offi'


@patch('app.importers.office_importer.mammoth.convert_to_html')
@patch('app.importers.office_importer.html2text.HTML2Text')
def test_office_importer_read_preview_docx(mock_html2text_class, mock_convert):
    mock_convert.return_value.value = '<p>Hello Docx World</p>'
    mock_html2text_instance = mock_html2text_class.return_value
    mock_html2text_instance.handle.return_value = '# Hello Docx World'
    importer = OfficeImporter()
    preview = importer.read_preview('file.docx', 15)
    assert preview == '# Hello Docx W'


@patch('app.importers.office_importer.textract.process')
def test_office_importer_read_preview_empty(mock_process):
    mock_process.return_value = b''
    importer = OfficeImporter()
    preview = importer.read_preview('file.doc', 10)
    assert preview is None


@patch('app.importers.office_importer.textract.process')
def test_office_importer_read_preview_exception(mock_process):
    mock_process.side_effect = Exception('Error')
    importer = OfficeImporter()
    preview = importer.read_preview('file.doc', 10)
    assert preview is None


def test_office_importer_reads_generated_docx(tmp_path):
    
    docx_path = tmp_path / "receipt.docx"
    doc = docx.Document()
    doc.add_paragraph('Receipt')
    doc.save(docx_path)

    importer = OfficeImporter()
    preview = importer.read_preview(str(docx_path), 10)
    assert preview and 'Receipt' in preview