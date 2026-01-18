from __future__ import annotations

from .interface import PeekImporter

import textract


class EBookImporter:
    """Extracts text previews from eBook files (.epub, .mobi)."""

    _SUPPORTED_MIMES = {
        'application/epub+zip',  # .epub
        'application/x-mobipocket-ebook',  # .mobi
    }

    def supports(self, path: str, mime: str) -> bool:
        return mime in self._SUPPORTED_MIMES

    def read_preview(self, path: str, limit: int) -> str | None:
        try:
            text = textract.process(path).decode('utf-8', errors='ignore')
            if not text.strip():
                return None
            return text[:limit]
        except Exception:
            return None


def build() -> PeekImporter:
    return EBookImporter()


__all__ = ["EBookImporter", "build"]