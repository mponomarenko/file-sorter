import pytest
from unittest.mock import patch
from app.importers.rtf_importer import RTFImporter


class TestRTFImporter:
    def test_supports_rtf(self):
        importer = RTFImporter()
        assert importer.supports('file.rtf', 'application/rtf')
        assert importer.supports('file.doc', 'application/rtf')
        assert not importer.supports('file.rtf', 'text/plain')

    @patch('app.importers.rtf_importer.textract.process')
    def test_read_preview_rtf(self, mock_textract):
        mock_textract.return_value = b'RTF document content'
        importer = RTFImporter()
        preview = importer.read_preview('file.rtf', 15)
        assert preview == 'RTF document co'

    def test_read_preview_no_text(self):
        importer = RTFImporter()
        preview = importer.read_preview('nonexistent.rtf', 10)
        assert preview is None