"""Folder action policy: decide whether to keep folders intact or disaggregate them."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
import os
import re
from typing import Iterable, Mapping

from .classifiers import Classifier
from .classifiers.rules import RulesClassifier
from .classifiers.base import FolderActionResponse
from .folder_action import FolderAction, RequiresAI, FolderActionRequest
from .utils import log


FOLDER_SAMPLE_LIMIT = 48


@dataclass
class FolderSample:
    # Track DIRECT children only (immediate subdirs and files)
    children: dict[str, dict] = field(default_factory=dict)  # name -> {type, mime, size, count}
    total_files: int = 0
    max_depth: int = 0

    def add_child(self, name: str, is_dir: bool, mime: str = "*", size: int = 0) -> None:
        """Add or update a direct child (file or immediate subdir)."""
        if name not in self.children:
            self.children[name] = {
                "type": "dir" if is_dir else "file",
                "mime": mime if not is_dir else "*",
                "size": size,
                "file_count": 0 if is_dir else 1,
            }
        if is_dir:
            self.children[name]["file_count"] += 1

    def payload(self, folder_path: str) -> dict:
        """Build payload like 'ls -lh' output: just direct children with size/type."""
        children_list = []
        for name, info in sorted(self.children.items())[:FOLDER_SAMPLE_LIMIT]:
            entry = {"name": name, "type": info["type"]}
            if info["type"] == "file":
                entry["mime"] = info["mime"]
                entry["size"] = info["size"]
            else:
                entry["files_inside"] = info["file_count"]
            children_list.append(entry)

        # Extract just the folder name for clarity
        folder_name = folder_path.rstrip('/').split('/')[-1] if '/' in folder_path else folder_path

        return {
            "folder_name": folder_name,
            "folder_path": folder_path,
            "children": children_list,
            "total_files": self.total_files,
            "child_count": len(self.children),
        }


def collect_folder_samples(rows: Iterable[tuple[str, str, int]]) -> dict[str, FolderSample]:
    """Collect folder samples showing only DIRECT children (like ls -lh)."""
    samples: dict[str, FolderSample] = {}

    for path, mime, size in rows:
        try:
            p = PurePosixPath(path)
        except Exception:
            continue

        current = p.parent
        parts_from_current = [p.name]

        while current and str(current) not in ("", "/"):
            folder_path = current.as_posix()
            sample = samples.setdefault(folder_path, FolderSample())
            sample.total_files += 1

            direct_child = parts_from_current[0]
            is_subdir = len(parts_from_current) > 1

            if is_subdir:
                sample.add_child(direct_child, is_dir=True, mime="*", size=0)
            else:
                sample.add_child(direct_child, is_dir=False, mime=mime, size=size)

            parts_from_current.insert(0, current.name)
            current = current.parent

    return samples


def normalize_action_map(action_map: Mapping[str, FolderAction | str] | None) -> dict[str, FolderAction]:
    """Normalize a map of folder actions to FolderAction values."""
    normalized: dict[str, FolderAction] = {}
    if not action_map:
        return normalized
    for path, raw in action_map.items():
        try:
            normalized[path] = raw if isinstance(raw, FolderAction) else FolderAction.from_string(str(raw))
        except ValueError:
            log.warning("normalize_action_invalid", path=path, action=str(raw))
            normalized[path] = FolderAction.KEEP
    return normalized


def build_folder_action_map(
    rules: RulesClassifier | None,
    classifier: Classifier | None,
    samples: Mapping[str, FolderSample],
    sources: list[str] | None = None,
    source_wrapper_pattern: str | None = None,
) -> tuple[dict[str, FolderAction], dict[str, str]]:
    """Build map of folder paths to actions (keep/disaggregate/strip) as FolderAction values.

    Returns: (actions_map, decisions_map)
        - actions_map: path -> FolderAction
        - decisions_map: path -> decision source (e.g., "ai:disaggregate", "rule:keep", "inherited:keep")
    
    Processing order: parent folders before children (sorted by path depth)
    Optimization: Children of KEEP folders inherit KEEP automatically (never classified by AI)
    """
    actions: dict[str, FolderAction] = {}
    decisions: dict[str, str] = {}
    
    # Sort by depth to ensure parent folders are processed before children
    sorted_folders = sorted(samples.keys(), key=lambda p: p.count("/"))
    
    log.info("folder_action_start", total_folders=len(sorted_folders))

    for folder in sorted_folders:
        # Check if any parent blocks child evaluation (only KEEP does this)
        # KEEP_EXCEPT and DISAGGREGATE allow children to be evaluated
        blocking_parent, parent_action = _get_decided_parent(folder, actions)
        if blocking_parent:
            # Do NOT add to actions map - inheritance is implicit
            log.debug("folder_inherited_keep", folder=folder, parent=blocking_parent)
            continue
        
        folder_sample = samples[folder]
        action, decision_source = _decide_folder_action(folder, folder_sample, rules, classifier, sources, actions, source_wrapper_pattern)
        
        if action is not None:
            actions[folder] = action
            decisions[folder] = decision_source or "unknown"
            log.info("folder_action_decided", 
                    folder=folder, 
                    action=action.value, 
                    source=decision_source or "unknown")
    
    log.info("folder_action_complete", 
            total_processed=len(actions), 
            total_skipped=0)
    return actions, decisions


def _get_decided_parent(folder: str, decided: dict[str, FolderAction]) -> tuple[str, FolderAction] | tuple[None, None]:
    """Get the parent folder that blocks child evaluation, if any.
    
    Returns (parent_path, parent_action) or (None, None).
    
    Blocking actions:
    - KEEP: children inherit KEEP (entire structure preserved)
    
    Non-blocking actions:
    - KEEP_EXCEPT: children are evaluated independently
    - DISAGGREGATE: children are evaluated independently
    """
    parts = folder.rstrip("/").split("/")
    for i in range(1, len(parts)):
        parent_path = "/".join(parts[:i])
        parent_action = decided.get(parent_path)
        if parent_action == FolderAction.KEEP:
            # Only KEEP blocks children
            return parent_path, parent_action
        # KEEP_EXCEPT and DISAGGREGATE do NOT block - continue checking ancestors
    return None, None


def _strip_sources(folder_path: str, sources: list[str], source_wrapper_pattern: str | None) -> str:
    """Strip source prefix from folder path to get relative path."""
    import os

    norm_path = os.path.normcase(os.path.normpath(folder_path))
    best_rel = folder_path
    best_len = -1

    for raw_root in sources or []:
        root = (raw_root or "").strip()
        if not root:
            continue
        norm_root = os.path.normcase(os.path.normpath(root))
        if norm_path == norm_root:
            return "/"
        prefix = norm_root + os.sep
        if norm_path.startswith(prefix) and len(norm_root) > best_len:
            rel = os.path.relpath(folder_path, root)
            best_rel = "/" + rel if rel != "." else "/"
            best_len = len(norm_root)

    rel_path = best_rel.strip("/")
    parts = [p for p in rel_path.split("/") if p]
    if parts and source_wrapper_pattern:
        pattern = re.compile(source_wrapper_pattern, re.IGNORECASE)
        if pattern.fullmatch(parts[0]):
            parts = parts[1:]
    if not parts:
        return "/"
    return "/" + "/".join(parts)


def _decide_folder_action(
    folder: str,
    folder_sample: FolderSample | None,
    rules: RulesClassifier | None,
    classifier: Classifier | None,
    sources: list[str] | None,
    decided: dict[str, FolderAction],
    source_wrapper_pattern: str | None,
) -> tuple[FolderAction, str]:
    """Orchestrate folder action decision through classifier chain.
    
    Chain: RulesClassifier → AI Classifier → Default
    Each classifier can either:
    - Make a decision (stop chain)
    - Delegate with hint (continue to next)
    
    Returns: (action, decision_source) or (None, None) if no decision
    
    Note: Parent folder check is done by caller to avoid redundant checks
    """
    if not folder_sample:
        return FolderAction.KEEP, "default:keep:empty_folder"
    
    # Build request
    rel_folder = _strip_sources(folder, sources or [], source_wrapper_pattern)
    payload = folder_sample.payload(rel_folder)
    request = FolderActionRequest(
        folder_path=payload["folder_path"],
        folder_name=payload["folder_name"],
        children=payload["children"],
        total_files=payload["total_files"],
        rule_hint=None,
    )
    
    # Chain of classifiers
    classifiers_chain: list[tuple[str, Classifier]] = []
    if rules:
        classifiers_chain.append(("rules", rules))
    if classifier:
        classifiers_chain.append(("ai", classifier))
    
    # Walk the chain
    for classifier_name, current_classifier in classifiers_chain:
        response = current_classifier.advise_folder_action(request)
        
        if response.is_final:
            # Classifier made decision - stop chain
            if response.action is None:
                log.error("folder_decision_invalid", folder=folder, classifier=classifier_name, reason="final_but_no_action")
                continue
            log.debug("folder_decision_final", 
                     folder=folder, 
                     classifier=classifier_name,
                     action=response.action,
                     reason=response.reason)
            return response.action, f"{classifier_name}:{response.action.value}:{response.reason}"
        
        # Classifier delegated - update hint and continue
        log.debug("folder_decision_delegate", 
                 folder=folder,
                 classifier=classifier_name,
                 hint=response.hint,
                 reason=response.reason)
        request.rule_hint = response.hint
    
    # Chain exhausted - use last hint or disaggregate
    action = request.rule_hint or FolderAction.DISAGGREGATE
    log.warning("folder_chain_exhausted", folder=folder, fallback_action=action)
    return action, f"default:chain_exhausted:{action}"
