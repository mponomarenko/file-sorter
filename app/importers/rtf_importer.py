from __future__ import annotations

from .interface import PeekImporter

import textract


class RTFImporter:
    """Extracts text previews from RTF files."""

    _SUPPORTED_MIME = "application/rtf"

    def supports(self, path: str, mime: str) -> bool:
        return mime == self._SUPPORTED_MIME

    def read_preview(self, path: str, limit: int) -> str | None:
        try:
            text = textract.process(path).decode('utf-8', errors='ignore')
            if not text.strip():
                return None
            return text[:limit]
        except Exception:
            return None


def build() -> PeekImporter:
    return RTFImporter()


__all__ = ["RTFImporter", "build"]