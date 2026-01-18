from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from app.categories import CategoryPath
from app.config import AppConfig
from app.folder_action import FolderAction
from app.folder_policy import collect_folder_samples, build_folder_action_map, normalize_action_map
from app.classifiers import RulesClassifier
from app.file_metadata import FileMetadata
from app.file_nodes import FileNodeBuilder
from app.media import MediaHelper


def _category_path(value: str) -> CategoryPath:
    parts = [part for part in value.split("/") if part]
    return CategoryPath(*parts)


def _build_metadata(payload: Mapping[str, Any] | None) -> FileMetadata:
    meta = FileMetadata()
    if not payload:
        return meta
    for key, value in payload.items():
        meta.add(key, value)
    return meta


@dataclass
class FileCase:
    path: str
    mime: str
    category: str
    expected: str
    metadata: Mapping[str, Any] | None = None
    folder_actions: Mapping[str, str] | None = None


@dataclass
class FolderCase:
    name: str
    files: list[FileCase]
    strip_dirs: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=lambda: ["/sources/src1"])
    main_target: str = "/target"
    folder_actions: Mapping[str, str] | None = None
    xfail_reason: str | None = None


class FolderCaseRunner:
    def __init__(self, case: FolderCase):
        self.case = case
        base_cfg = AppConfig.from_env()
        self.cfg = replace(
            base_cfg,
            STRIP_DIRS=list(case.strip_dirs),
            SOURCES=list(case.sources),
            MAIN_TARGET=case.main_target,
            CLASSIFIER_KIND="manual",
        )
        self.media = MediaHelper(self.cfg)
        self._rules = RulesClassifier()
        self._base_actions = self._build_base_actions()

    def run(self) -> None:
        failures: list[str] = []
        before_after: list[str] = []

        for file_case in self.case.files:
            builder = FileNodeBuilder(
                sources=self.cfg.SOURCES,
                folder_action_map=self._folder_actions_for(file_case),
                source_wrapper_pattern=self.cfg.SOURCE_WRAPPER_REGEX,
            )
            path = self._abs_path(file_case.path)
            node = builder.build(
                path,
                category=_category_path(file_case.category),
                mime=file_case.mime,
                metadata=_build_metadata(file_case.metadata),
                rule_match=None,
            )
            destination = self.media.build_destination(node)
            before_after.append(f"{path} -> {destination.destination}")
            if destination.destination != file_case.expected:
                failures.append(
                    f"{file_case.path}: expected {file_case.expected}, got {destination.destination}"
                )

        if failures:
            message = "\n".join(
                [
                    f"Folder case '{self.case.name}' failed:",
                    *failures,
                    "",
                    "Planned moves:",
                    *before_after,
                ]
            )
            raise AssertionError(message)

    def _folder_actions_for(self, file_case: FileCase) -> Mapping[str, FolderAction]:
        actions: dict[str, FolderAction] = dict(self._base_actions)
        for table in (self.case.folder_actions, file_case.folder_actions):
            if not table:
                continue
            for rel_path, action in table.items():
                actions[self._abs_path(rel_path)] = FolderAction.from_string(action)
        return actions

    def _abs_path(self, relative: str) -> str:
        rel = relative.strip("/")
        source = self.case.sources[0] if self.case.sources else "/sources/src1"
        return f"{source.rstrip('/')}/{rel}"

    def _build_base_actions(self) -> dict[str, FolderAction]:
        """Build baseline folder actions from rules for all files in the case."""
        rows = [(self._abs_path(fc.path), fc.mime, 0) for fc in self.case.files]
        samples = collect_folder_samples(rows)
        actions, _ = build_folder_action_map(
            self._rules,
            None,
            samples,
            self.cfg.SOURCES,
            self.cfg.SOURCE_WRAPPER_REGEX,
        )
        return normalize_action_map(actions)
