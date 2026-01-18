from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


class FileMetadata:
    """Container for file metadata with helpers for template rendering."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    def add(self, key: str, value: Any) -> None:
        normalized = self._normalize_value(value)
        if normalized is None:
            return
        self._data[key] = normalized

    def add_missing(self, key: str, value: Any) -> None:
        if key not in self._data:
            self.add(key, value)

    def merge(self, mapping: Dict[str, Any]) -> None:
        for key, value in mapping.items():
            self.add(key, value)

    def update(self, mapping: Dict[str, Any]) -> None:
        if not mapping:
            return
        self.merge(mapping)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    def _normalize_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            for item in value:
                normalized = self._normalize_value(item)
                if normalized is not None:
                    return normalized
            return None
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", "ignore")
            except Exception:
                value = value.decode("latin-1", "ignore")
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            return text
        if isinstance(value, datetime):
            return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for k, v in value.items():
                normalized = self._normalize_value(v)
                if normalized is not None:
                    out[k] = normalized
            return out or None
        return value
