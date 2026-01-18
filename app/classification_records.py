from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .categories import CategoryPath
from .config import AppConfig
from .file_nodes import FileNode, serialize_file_node
from .path_models import ClassifiedPath


def _to_category_path(value: CategoryPath | str | None) -> CategoryPath | None:
    if value is None:
        return None
    if isinstance(value, CategoryPath):
        return value
    return CategoryPath(value)


@dataclass(frozen=True)
class ClassificationRecord:
    """Keep classification persistence payloads in sync across planner/CLI/DB paths."""

    category_path: CategoryPath | None
    destination: str | None
    path: str
    rule_category: CategoryPath | None = None
    ai_category: CategoryPath | None = None
    metadata_json: str | None = None
    preview: str | None = None
    file_json: str | None = None
    _allow_empty_category: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self):
        if self._allow_empty_category:
            return
        if self.category_path is None or not str(self.category_path).strip():
            raise ValueError("ClassificationRecord requires a non-empty category label")

    @property
    def category_label(self) -> str | None:
        return self.category_path.label if self.category_path else None

    @property
    def rule_category_label(self) -> str | None:
        return self.rule_category.label if self.rule_category else None

    @property
    def ai_category_label(self) -> str | None:
        return self.ai_category.label if self.ai_category else None

    def as_db_tuple(self) -> tuple[str | None, str | None, str | None, str | None, str | None, str | None, str | None, str]:
        return (
            self.category_label,
            self.destination,
            self.rule_category_label,
            self.ai_category_label,
            self.metadata_json,
            self.preview,
            self.file_json,
            self.path,
        )

    def parsed_metadata(self) -> Any | None:
        if not self.metadata_json:
            return None
        try:
            return json.loads(self.metadata_json)
        except json.JSONDecodeError:
            return None

    def parsed_file_node(self) -> Any | None:
        if not self.file_json:
            return None
        try:
            return json.loads(self.file_json)
        except json.JSONDecodeError:
            return None

    def export(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "destination": self.destination,
            "category": self.category_label,
            "rule_category": self.rule_category_label,
            "ai_category": self.ai_category_label,
            "preview": self.preview,
        }
        metadata = self.parsed_metadata()
        if metadata is not None:
            payload["metadata"] = metadata
        elif self.metadata_json:
            payload["metadata_raw"] = self.metadata_json
        file_node = self.parsed_file_node()
        if file_node is not None:
            payload["file_node"] = file_node
        elif self.file_json:
            payload["file_node_raw"] = self.file_json
        return payload

    @classmethod
    def from_db_row(cls, row: tuple[str, str | None, str | None, str | None, str | None, str | None, str | None, str]) -> "ClassificationRecord":
        path, dest, category, rule_category, ai_category, metadata_json, preview, file_json = row
        return cls(
            category_path=_to_category_path(category),
            destination=dest,
            path=path,
            rule_category=_to_category_path(rule_category),
            ai_category=_to_category_path(ai_category),
            metadata_json=metadata_json,
            preview=preview,
            file_json=file_json,
            _allow_empty_category=True,
        )


@dataclass
class ClassificationRecordBuilder:
    cfg: AppConfig

    def build(self, file_node: FileNode, destination: ClassifiedPath) -> ClassificationRecord:
        metadata_json = json.dumps(file_node.metadata.to_dict(), ensure_ascii=False)
        file_json = json.dumps(serialize_file_node(file_node), ensure_ascii=False)
        preview = (file_node.preview or "")[: self.cfg.MAX_CONTENT_PEEK]
        return ClassificationRecord(
            category_path=file_node.category,
            destination=destination.destination,
            path=file_node.physical_path.as_posix(),
            rule_category=file_node.rule_category,
            ai_category=file_node.ai_category,
            metadata_json=metadata_json,
            preview=preview,
            file_json=file_json,
        )
