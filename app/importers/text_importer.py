from __future__ import annotations

from .interface import PeekImporter


class TextFileImporter:
    """Extracts previews from plain-text oriented formats."""

    _SUPPORTED_MIME_PREFIX = "text/"
    _SUPPORTED_MIME_SET = {"application/json", "application/xml"}

    def supports(self, path: str, mime: str) -> bool:
        if not mime:
            return False
        if mime.startswith(self._SUPPORTED_MIME_PREFIX):
            return True
        return mime in self._SUPPORTED_MIME_SET

    def read_preview(self, path: str, limit: int) -> str | None:
        if limit is not None and limit < 0:
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                if limit:
                    return handle.read(limit)
                return handle.read()
        except OSError:
            return None


def build() -> PeekImporter:
    return TextFileImporter()


__all__ = ["TextFileImporter", "build"]
