from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import PurePosixPath, PurePath
from typing import Any, Mapping, Sequence, Tuple

from .categories import CategoryPath
from .file_metadata import FileMetadata
from .folder_action import FolderAction
from .folder_policy import normalize_action_map
from .rules_models import RuleMatch


def _parts(p: PurePath) -> Tuple[str, ...]:
    return tuple(part for part in p.parts if part not in ("", "/"))


def _resolve_source_root(path: PurePosixPath, sources: Sequence[str] | None) -> tuple[PurePosixPath, Tuple[str, ...], Tuple[str, ...]]:
    best_root: PurePosixPath | None = None
    best_rel: PurePosixPath | None = None
    best_len = -1
    for raw in sources or []:
        raw_clean = (raw or "").strip()
        if not raw_clean:
            continue
        root = PurePosixPath(raw_clean)
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        root_len = len(_parts(root))
        if root_len > best_len:
            best_root = root
            best_rel = rel
            best_len = root_len
    if best_root is None or best_rel is None:
        return PurePosixPath("/"), (), _parts(path)
    return best_root, _parts(best_root), _parts(best_rel)


def _folder_actions_for(
    physical_path: PurePosixPath,
    action_map: Mapping[str, FolderAction] | None,
) -> dict[str, FolderAction]:
    """Extract folder actions for all parent directories of physical_path.
    
    Returns actions keyed by physical disk paths (not logical stripped paths).
    This ensures the actions can be matched against entry.abs_path during walk.
    """
    if not action_map:
        return {}
    actions: dict[str, FolderAction] = {}
    current = physical_path.parent
    while current != current.parent:  # Stop at root
        action = action_map.get(current.as_posix())
        if action:
            actions[current.as_posix()] = action
        current = current.parent
    return actions


@dataclass(frozen=True)
class FolderRef:
    """Thin reference to a folder so we can cache decisions without materialising the full tree."""
    path: PurePosixPath
    chain: CategoryPath
    action: FolderAction | None = None


@dataclass(frozen=True)
class FileNode:
    """
    Canonical representation of a file flowing through the system.

    We keep both the original rule-provided category and the AI-refined category
    so that we can audit and recompute classifications later without re-running
    the entire pipeline.
    """
    physical_path: PurePosixPath
    source_root: PurePosixPath
    source_prefix: Tuple[str, ...]
    relative_parts: Tuple[str, ...]
    source_chain: CategoryPath
    folder: FolderRef
    category: CategoryPath
    mime: str
    metadata: FileMetadata
    folder_actions: Mapping[str, FolderAction]
    rule_category: CategoryPath | None = None
    ai_category: CategoryPath | None = None
    rule_match: RuleMatch | None = None
    classifier_origin: str | None = None
    preview: str | None = None
    folder_decisions: Mapping[str, str] | None = None  # path -> decision_source (e.g., "ai:disaggregate", "rule:keep")
    folder_details: list[dict] | None = None  # detailed decision chain for each folder

    @property
    def relative_dirs(self) -> Tuple[str, ...]:
        if not self.relative_parts:
            return ()
        return self.relative_parts[:-1]


class FileNodeBuilder:
    """Builder that centralizes FileNode construction from planner/CLI inputs."""

    def __init__(
        self,
        *,
        sources: Sequence[str] | None = None,
        folder_action_map: Mapping[str, FolderAction] | None = None,
        folder_decisions: Mapping[str, str] | None = None,
        folder_details: list[dict] | None = None,
        source_wrapper_pattern: str | None = None,
    ):
        self._sources = tuple(sources or ())
        self._folder_actions = normalize_action_map(folder_action_map)
        self._folder_decisions = dict(folder_decisions or {})
        self._folder_details = list(folder_details or [])
        self._wrapper_pattern = re.compile(source_wrapper_pattern, re.IGNORECASE) if source_wrapper_pattern else None

    def build(
        self,
        path: str,
        *,
        category: CategoryPath,
        rule_category: CategoryPath | None = None,
        ai_category: CategoryPath | None = None,
        mime: str,
        metadata: FileMetadata,
        rule_match: RuleMatch | None,
        classifier_origin: str | None = None,
        preview: str | None = None,
    ) -> FileNode:
        physical = PurePosixPath(path)
        source_root, root_parts, rel_parts = _resolve_source_root(physical, self._sources)
        if not rel_parts:
            rel_parts = _parts(physical)

        source_prefix = root_parts
        stripped_parts = rel_parts
        if rel_parts and self._wrapper_pattern and self._wrapper_pattern.fullmatch(rel_parts[0]):
            source_prefix = tuple((*root_parts, rel_parts[0]))
            stripped_parts = rel_parts[1:]

        chain_parts = tuple(part for part in (*source_prefix, *stripped_parts) if part)
        source_chain = CategoryPath(*chain_parts)
        folder_chain_parts = chain_parts[:-1] or chain_parts
        folder_chain = CategoryPath(*folder_chain_parts)
        folder_action = self._folder_actions.get(physical.parent.as_posix())
        folder_ref = FolderRef(
            path=physical.parent,
            chain=folder_chain,
            action=folder_action,
        )
        folder_actions = _folder_actions_for(physical, self._folder_actions)
        return FileNode(
            physical_path=physical,
            source_root=source_root,
            source_prefix=source_prefix,
            relative_parts=stripped_parts,
            source_chain=source_chain,
            category=category,
            rule_category=rule_category,
            ai_category=ai_category,
            folder=folder_ref,
            mime=mime,
            metadata=metadata,
            folder_actions=folder_actions,
            folder_decisions=self._folder_decisions,
            folder_details=self._folder_details,
            rule_match=rule_match,
            preview=preview,
            classifier_origin=classifier_origin,
        )


def serialize_file_node(file_node: FileNode) -> dict[str, Any]:
    """Return a JSON-serializable dict capturing key attributes for persistence/auditing."""
    return {
        "physical_path": file_node.physical_path.as_posix(),
        "source_root": file_node.source_root.as_posix(),
        "source_prefix": list(file_node.source_prefix),
        "relative_parts": list(file_node.relative_parts),
        "source_chain": file_node.source_chain.label,
        "category": str(file_node.category),
        "rule_category": str(file_node.rule_category) if file_node.rule_category else None,
        "ai_category": str(file_node.ai_category) if file_node.ai_category else None,
        "folder": {
            "path": file_node.folder.path.as_posix(),
            "chain": file_node.folder.chain.label,
            "action": str(file_node.folder.action) if file_node.folder.action else None,
        },
        "mime": file_node.mime,
        "metadata": file_node.metadata.to_dict(),
        "folder_actions": {path: str(action) for path, action in file_node.folder_actions.items()},
        "folder_decisions": dict(file_node.folder_decisions) if file_node.folder_decisions else {},
        "preview": file_node.preview,
        "classifier_origin": file_node.classifier_origin,
    }
