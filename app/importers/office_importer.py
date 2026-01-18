from __future__ import annotations

from .interface import PeekImporter

from contextlib import nullcontext
from pathlib import Path

import html2text
import mammoth
import textract


def _docx_to_markdown(path: str) -> str:
    """Fallback to path passing when file is absent so tests can stub mammoth."""
    source = Path(path)
    context = source.open('rb') if source.exists() else nullcontext(path)
    with context as handle:
        html = mammoth.convert_to_html(handle).value
    parser = html2text.HTML2Text()
    parser.ignore_links = True
    parser.ignore_images = True
    return parser.handle(html)


def _truncate_preview(text: str, limit: int) -> str:
    preview = text[:limit]
    if len(text) > limit and preview and preview[-1].isalnum() and text[limit].isalnum():
        return preview[:-1]
    return preview


class OfficeImporter:
    """Extracts text previews from MS Office and similar documents, ideally in MD format."""

    _SUPPORTED_MIMES = {
        'application/msword',  # .doc
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # .docx
        'application/vnd.ms-excel',  # .xls
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
        'application/vnd.ms-powerpoint',  # .ppt
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',  # .pptx
        'application/vnd.oasis.opendocument.text',  # .odt
        'application/vnd.oasis.opendocument.spreadsheet',  # .ods
        'application/vnd.oasis.opendocument.presentation',  # .odp
    }

    def supports(self, path: str, mime: str) -> bool:
        return mime in self._SUPPORTED_MIMES

    def read_preview(self, path: str, limit: int) -> str | None:
        try:
            if path.endswith('.docx'):
                # Mammoth keeps docx structure consistent with our markdown classifiers
                text = _docx_to_markdown(path)
                text = text.strip()
                if not text:
                    return None
                return _truncate_preview(text, limit)
            else:
                # Textract handles legacy office binaries without additional work
                text = textract.process(path).decode('utf-8', errors='ignore')
                text = text.strip()
                if not text:
                    return None
                return text[:limit]
        except Exception:
            return None


def build() -> PeekImporter:
    return OfficeImporter()


__all__ = ["OfficeImporter", "build"]
