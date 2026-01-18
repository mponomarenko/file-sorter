import pytest
from unittest.mock import patch
from app.importers.ebook_importer import EBookImporter


class TestEBookImporter:
    def test_supports_epub(self):
        importer = EBookImporter()
        assert importer.supports('file.epub', 'application/epub+zip')
        assert importer.supports('file.mobi', 'application/epub+zip')

    def test_supports_mobi(self):
        importer = EBookImporter()
        assert importer.supports('file.mobi', 'application/x-mobipocket-ebook')
        assert importer.supports('file.epub', 'application/x-mobipocket-ebook')

    def test_supports_other(self):
        importer = EBookImporter()
        assert not importer.supports('file.pdf', 'application/pdf')

    @patch('app.importers.ebook_importer.textract.process')
    def test_read_preview_epub(self, mock_textract):
        mock_textract.return_value = b'EBook content here'
        importer = EBookImporter()
        preview = importer.read_preview('file.epub', 12)
        assert preview == 'EBook conten'

    @patch('app.importers.ebook_importer.textract.process')
    def test_read_preview_mobi(self, mock_textract):
        mock_textract.return_value = b'Mobi book text'
        importer = EBookImporter()
        preview = importer.read_preview('file.mobi', 10)
        assert preview == 'Mobi book '

    def test_read_preview_no_text(self):
        importer = EBookImporter()
        preview = importer.read_preview('nonexistent.epub', 10)
        assert preview is None