from dataclasses import dataclass
from typing import Match, Optional, Pattern

from .categories import CategoryPath
from .folder_action import FolderAction, RequiresAI


@dataclass
class CompiledRule:
    path_pattern: Optional[str]
    mime_pattern: Optional[str]
    path_regex: Optional[Pattern]
    mime_regex: Optional[Pattern]
    category_path: CategoryPath
    folder_action: Optional[FolderAction]
    requires_ai: RequiresAI
    line_number: Optional[int] = None

    def match(self, rel_path: str, mime: str) -> Optional[tuple[Match | None, Match | None]]:
        path_match: Match | None = None
        mime_match: Match | None = None
        if self.path_regex:
            path_match = self.path_regex.match(rel_path)
            if not path_match:
                return None
        if self.mime_regex:
            mime_match = self.mime_regex.match(mime)
            if not mime_match:
                return None
        return path_match, mime_match


@dataclass
class RuleMatch:
    rule: CompiledRule
    path_match: Match | None
    mime_match: Match | None

    def named_groups(self) -> dict[str, str]:
        data: dict[str, str] = {}
        if self.path_match:
            for key, value in self.path_match.groupdict().items():
                if value is not None:
                    data[key] = value
        if self.mime_match:
            for key, value in self.mime_match.groupdict().items():
                if value is not None and key not in data:
                    data[key] = value
        return data
