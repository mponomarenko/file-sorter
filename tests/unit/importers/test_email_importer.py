import pytest
from unittest.mock import patch, mock_open
from app.importers.email_importer import EmailImporter


class TestEmailImporter:
    def test_supports_eml(self):
        importer = EmailImporter()
        assert importer.supports('file.eml', 'message/rfc822')
        assert importer.supports('file.msg', 'message/rfc822')
        assert not importer.supports('file.eml', 'text/plain')

    def test_supports_msg(self):
        importer = EmailImporter()
        assert importer.supports('file.msg', 'application/vnd.ms-outlook')
        assert importer.supports('file.eml', 'application/vnd.ms-outlook')

    @patch('app.importers.email_importer.textract.process')
    def test_read_preview_msg(self, mock_textract):
        mock_textract.return_value = b'Email content from msg'
        importer = EmailImporter()
        preview = importer.read_preview('file.msg', 20)
        assert preview == 'Email content from m'

    @patch('builtins.open', new_callable=mock_open)
    @patch('app.importers.email_importer.email.message_from_binary_file')
    def test_read_preview_eml(self, mock_email, mock_file):
        mock_msg = mock_email.return_value
        mock_msg.get.return_value = 'Test Subject'
        mock_msg.is_multipart.return_value = False
        mock_msg.get_content_type.return_value = 'text/plain'
        mock_msg.get_content_charset.return_value = 'utf-8'
        mock_msg.get_payload.return_value = b'Email body text'

        importer = EmailImporter()
        preview = importer.read_preview('file.eml', 30)
        assert preview == 'Subject: Test Subject\nEmail bo'

    def test_read_preview_no_text(self):
        importer = EmailImporter()
        preview = importer.read_preview('nonexistent.eml', 10)
        assert preview is None