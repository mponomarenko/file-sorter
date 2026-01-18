from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PeekImporter(Protocol):
    """Interface for extracting textual previews from files."""

    def supports(self, path: str, mime: str) -> bool:
        """Return True when this importer can handle the given file."""

    def read_preview(self, path: str, limit: int) -> str | None:
        """Return up to `limit` characters of text or None when unavailable."""


__all__ = ["PeekImporter"]
