import os
import re
import mimetypes
import pathlib
from pathlib import PurePosixPath
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from blake3 import blake3

from .categories import CategoryPath
from .utils import log
from .config import AppConfig
from .file_metadata import FileMetadata
from .rules_models import RuleMatch
from .classifiers import RulesClassifier
from .path_models import ClassifiedPath, FullPath
from .file_nodes import FileNode
from .folder_action import FolderAction
from .importers.interface import PeekImporter
from .importers.text_importer import build as build_text_importer
from .importers.pdf_importer import build as build_pdf_importer
from .importers.office_importer import build as build_office_importer
from .importers.ocr_importer import build as build_ocr_importer
from .importers.email_importer import build as build_email_importer
from .importers.rtf_importer import build as build_rtf_importer
from .importers.ebook_importer import build as build_ebook_importer


@dataclass
class _DirEntry:
    value: str
    lower: str
    abs_path: str
    folder_action: FolderAction | None = None
    action_source: str | None = None

def blake3_hash(path: str) -> str | None:
    try:
        h = blake3()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except PermissionError as e:
        log.warning(f"Permission error reading for hash: {path} : {e}")
        return None
    except FileNotFoundError as e:
        log.warning(f"File not found reading for hash: {path} : {e}")
        return None
    except Exception as e:
        log.warning(f"Error reading for hash: {path} : {e}")
        return None


def detect_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    try:
        out = subprocess.check_output(
            ["file", "--brief", "--mime-type", path], text=True
        ).strip()
        return out if out else "application/octet-stream"
    except Exception:
        return "application/octet-stream"


_IMPORTERS: list[PeekImporter] = [
    build_text_importer(),
    build_pdf_importer(),
    build_office_importer(),
    build_ocr_importer(),
    build_email_importer(),
    build_rtf_importer(),
    build_ebook_importer(),
]


def peek_text(path: str, mime: str, limit: int) -> str:
    for importer in _IMPORTERS:
        try:
            if not importer.supports(path, mime):
                continue
            preview = importer.read_preview(path, limit or 0)
            if preview:
                if limit:
                    return preview[:limit]
                return preview
        except Exception as exc:
            log.warning(
                "peek_text_error",
                path=path,
                importer=importer.__class__.__name__,
                error=str(exc),
            )
    return ""


class MediaHelper:
    """
    MediaHelper is the only component that understands how a classified file maps
    into the canonical destination tree. By keeping the path building logic in
    one place we can recompute destinations later (e.g. after reclassification)
    without duplicating heuristics across the planner, CLIs, or copy tooling.
    """

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.categories = cfg.categories
        pattern_text = cfg.SOURCE_WRAPPER_REGEX or ""
        compiled = None
        if pattern_text:
            try:
                compiled = re.compile(pattern_text, re.IGNORECASE)
            except re.error:
                log.warning("invalid_source_wrapper_regex", pattern=pattern_text)
        self._source_wrapper_pattern = compiled
        self._rules_classifier = RulesClassifier()

    def _get_source_prefixes(self) -> list[tuple[str, ...]]:
        """Get normalized source prefixes to strip from paths."""
        prefixes: list[tuple[str, ...]] = []
        for raw in self.cfg.SOURCES + self.cfg.STRIP_DIRS:
            item = (raw or "").strip().strip("/")
            if item:
                parts = tuple(p.lower() for p in item.split("/") if p.strip())
                if parts:
                    prefixes.append(parts)
        # Sort by length descending to match longest first
        prefixes.sort(key=len, reverse=True)
        return prefixes

    def _collect_parent_entries(self, path: PurePosixPath) -> list[_DirEntry]:
        """Collect all parent directory entries with normalized names."""
        entries: list[_DirEntry] = []
        parts_accum: list[str] = []
        for part in path.parent.parts:
            part_text = str(part).strip("/")
            if not part_text or part_text.endswith(":"):
                continue
            parts_accum.append(part_text)
            abs_path = "/" + "/".join(parts_accum)
            entries.append(_DirEntry(part_text, part_text.lower(), abs_path))
        return entries

    def _label_entries(self, entries: list[_DirEntry], folder_actions: dict[str, FolderAction] | None) -> None:
        """Label each entry with keep/disaggregate based on a precomputed folder_action map."""
        if not entries:
            return
        if folder_actions:
            log.debug("folder_actions_map", keys=list(folder_actions.keys())[:5])
        prefix: list[str] = []
        for entry in entries:
            prefix.append(entry.value)
            lookup = "/" + "/".join(prefix)

            if folder_actions and entry.abs_path in folder_actions:
                log.debug("folder_action_lookup_hit", abs_path=entry.abs_path, action=folder_actions[entry.abs_path])
                action = folder_actions[entry.abs_path]
                entry.folder_action = action
                entry.action_source = "explicit"
                continue

            if folder_actions:
                log.debug("folder_action_lookup_miss", abs_path=entry.abs_path, lookup=lookup, available=list(folder_actions.keys())[:3])

            entry.folder_action = FolderAction.KEEP
            entry.action_source = None

    def _find_first_keep_index(self, entries: list[_DirEntry]) -> int | None:
        """Find first 'keep' or 'keep_except' marker - if ANY subfolder has keep, keep the whole subtree.
        
        KEEP_EXCEPT allows deeper disaggregate markers to override, but still marks the start
        of a kept structure.
        """
        for idx, entry in enumerate(entries):
            log.debug("checking_keep_index", idx=idx, path=entry.abs_path, folder_action=entry.folder_action)
            if entry.folder_action in (FolderAction.KEEP, FolderAction.KEEP_EXCEPT):
                log.info("found_keep_index", idx=idx, path=entry.abs_path, action=entry.folder_action)
                return idx
        log.debug("no_keep_found", entry_count=len(entries))
        return None

    def _strip_prefix(self, entries: list[_DirEntry], category_parts: tuple[str, ...]) -> int:
        """PASS 1: Top-down - Strip entries that match configured source prefixes or STRIP_DIRS."""
        source_prefixes = self._get_source_prefixes()
        category_parts_lower = tuple(p.lower() for p in category_parts)
        
        # Convert single-level prefixes to a set for fast lookup
        strip_names = set()
        for prefix in source_prefixes:
            if len(prefix) == 1:
                strip_names.add(prefix[0])
        
        entry_lower = [e.lower for e in entries]
        
        # First try to match any multi-level prefix from the start
        stripped = 0
        for prefix in source_prefixes:
            if len(prefix) > 1:
                if len(entries) >= len(prefix):
                    if all(entry_lower[i] == prefix[i] for i in range(len(prefix))):
                        stripped = len(prefix)
                        break
        
        # Then continue stripping single-level entries that are in the strip list
        for i in range(stripped, len(entries)):
            if entries[i].lower in strip_names:
                stripped = i + 1
            else:
                break  # Stop at first non-matching entry

        # Drop a single source wrapper (e.g., src1) so mount names don't leak into targets
        if self._source_wrapper_pattern and stripped < len(entries):
            candidate = entries[stripped].value
            if self._source_wrapper_pattern.fullmatch(candidate):
                stripped += 1

        # Strip category prefix to avoid duplicating category folders in destination when folder actions are absent
        cat_idx = 0
        idx = stripped
        while idx < len(entries) and cat_idx < len(category_parts_lower):
            if entries[idx].lower != category_parts_lower[cat_idx]:
                break
            stripped += 1
            idx += 1
            cat_idx += 1
        
        return stripped



    def build_destination(self, file_node: FileNode) -> ClassifiedPath:
        """Build destination path using two-pass algorithm from spec."""
        p = file_node.physical_path
        name = p.name
        category_path = file_node.category
        mime = file_node.mime
        metadata = file_node.metadata or FileMetadata()
        rule_match = file_node.rule_match
        folder_actions = file_node.folder_actions
        template = self.categories.template_for(category_path)

        # Collect and label all parent directories
        entries = self._collect_parent_entries(p)
        self._label_entries(entries, dict(folder_actions) if folder_actions else None)

        # PASS 1: Top-down - Strip prefix (sources/strip_dirs)
        strip_end = self._strip_prefix(entries, tuple(category_path.parts))
        log.debug("after_prefix_strip", strip_end=strip_end, entries_remaining=len(entries) - strip_end)
        
        # PASS 2: Find first keep marker in remaining path (after stripped prefix)
        keep_index_relative = self._find_first_keep_index(entries[strip_end:])
        keep_index = (strip_end + keep_index_relative) if keep_index_relative is not None else None

        # Split path into parts
        prefix_parts = [e.value for e in entries[:strip_end]]
        if keep_index is not None:
            disagg_parts = [e.value for e in entries[strip_end:keep_index]]

            keep_entry = entries[keep_index]
            if keep_entry.folder_action == FolderAction.KEEP_EXCEPT:
                kept_parts = []
                temp_disagg = []
                in_disaggregate_section = False

                for entry in entries[keep_index:]:
                    if entry.folder_action == FolderAction.DISAGGREGATE and entry.action_source == "explicit":
                        in_disaggregate_section = True
                        temp_disagg.append(entry.value)
                    elif in_disaggregate_section:
                        temp_disagg.append(entry.value)
                    else:
                        kept_parts.append(entry.value)

                disagg_parts.extend(temp_disagg)
                explicit_keep = any(
                    e.action_source == "explicit" for e in entries[keep_index:] if e.folder_action != FolderAction.DISAGGREGATE
                )
            else:
                kept_parts = [e.value for e in entries[keep_index:]]
                explicit_keep = any(e.action_source == "explicit" for e in entries[keep_index:])
        else:
            disagg_parts = [e.value for e in entries[strip_end:]]
            kept_parts = []
            explicit_keep = False

        # Build destination path
        base = pathlib.Path(self.cfg.MAIN_TARGET, *category_path.parts)
        
        # Check if this is an explicit template (not the __default__ fallback)
        is_default_template = template == self.categories.template_for(CategoryPath("__nonexistent__"))
        
        if template and not is_default_template and kept_parts:
            # Use template rendering when we have an explicit template AND kept structure
            # Template uses metadata fields and kept path
            kept_path_str = "/".join(kept_parts) if kept_parts else None
            rendered = self.categories.render_template(
                template,
                metadata.to_dict(),
                category_path=category_path,
                kept_path=kept_path_str,
                filename=name
            )
            destination = pathlib.Path(self.cfg.MAIN_TARGET) / rendered
        elif kept_parts:
            # We have kept folders - use kept path directly
            # (either no template, or using __default__ template which we skip for kept paths)
            destination = base / pathlib.Path(*kept_parts) / name
        elif template:
            # No kept parts, but we have a template (likely __default__)
            # Use template to organize flat files
            rendered = self.categories.render_template(
                template,
                metadata.to_dict(),
                category_path=category_path,
                kept_path=None,
                filename=name
            )
            destination = pathlib.Path(self.cfg.MAIN_TARGET) / rendered
        else:
            # No template, no kept parts - just category + filename
            # Disaggregated folders are tracked but not used in destination
            destination = base / name

        # Build full path tracking structure
        full_path = FullPath(
            original=p,
            source_prefix=tuple(prefix_parts),
            disaggregated=tuple(disagg_parts),
            kept=tuple(kept_parts),
            kept_role="keep" if kept_parts else "suffix",
            file=name,
        )

        return ClassifiedPath.build(
            destination=destination.as_posix(),
            category=category_path,
            metadata={"mime": mime},
            full_path=full_path,
        )

    def _build_template_context(
        self,
        category_path: CategoryPath,
        source_path: str,
        kept_parts: list[str],
        disagg_parts: list[str],
        mime: str,
        metadata: FileMetadata,
        rule_match: RuleMatch | None,
    ) -> dict[str, Any]:
        """Build template context with all available metadata.
        
        This prepares a context dict for template rendering in Categories.render_template().
        """
        p = pathlib.Path(source_path)
        
        # Base context with file information
        context: dict[str, Any] = {
            "category_path": category_path.label,
            "category": category_path.parts[0] if category_path.parts else "",
            "filename": p.name,
            "file_stem": p.stem,
            "extension": p.suffix.lstrip("."),
            "mime": mime,
        }
        
        # Add file metadata (artist, album, title, etc.)
        context.update(metadata.to_dict())
        
        # Add rule match named groups (captured from regex rules)
        if rule_match:
            context.update({k: v for k, v in rule_match.named_groups().items() if v is not None})
        
        # Add suffix (kept folder path) for templates that need it
        # Note: kept_parts are determined by the two-pass folder action algorithm
        # - Pass 1: strip sources/strip_dirs prefix
        # - Pass 2: find first "keep" marker -> everything from that point is kept
        context["suffix"] = "/".join(kept_parts) if kept_parts else None
        
        return context

    def safe_move(self, src: str, dst: str) -> str:
        dst_p = pathlib.Path(dst)
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        if self.cfg.RELINK_WITH_REFLINK:
            try:
                subprocess.check_call([
                    "cp",
                    "--reflink=always",
                    "--preserve=all",
                    src,
                    str(dst_p),
                ])
                os.utime(str(dst_p), (os.stat(src).st_atime, os.stat(src).st_mtime))
                os.remove(src)
                return "reflinked+removed"
            except Exception:
                pass
        shutil.move(src, dst)
        return "moved"

    # Removed unused _month_from_iso method.
