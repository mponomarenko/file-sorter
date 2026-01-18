import stat
import shlex
from collections import defaultdict, Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Any

from .categories import CategoryPath
from .classifiers import RulesClassifier
from .rules_models import CompiledRule


@dataclass
class PlannedItem:
    src: str
    dest: str
    size: int
    category_path: CategoryPath
    mime: str | None
    rule: CompiledRule | None
    preview: str | None = None

    @property
    def category_label(self) -> str:
        return self.category_path.label


def _format_bytes(num: int) -> str:
    if num <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    val = float(num)
    for unit in units:
        if val < 1024 or unit == units[-1]:
            return f"{val:.2f} {unit}"
        val /= 1024
    return f"{val:.2f} {units[-1]}"


def _format_gib(num: int) -> str:
    return f"{num / (1024 ** 3):.2f} GiB"


def _percent(part: int, total: int) -> str:
    if total <= 0:
        return "0.0"
    return f"{(part / total) * 100:.1f}"


def _quote(path: str) -> str:
    return shlex.quote(path)


def _normalise_rule(rule: CompiledRule | None) -> str:
    if not rule:
        return "LLM/manual"
    parts = []
    if rule.path_pattern:
        parts.append(f"path={rule.path_pattern}")
    if rule.mime_pattern:
        parts.append(f"mime={rule.mime_pattern}")
    return "; ".join(parts) if parts else "rule: <any>"


def _find_matching_rule(classifier: RulesClassifier, path: str, mime: str) -> CompiledRule | None:
    match = classifier.match("", path, mime)
    return match.rule if match else None


def build_copy_script(
    items_raw: Iterable[Sequence],
    classifier: RulesClassifier,
    script_path: Path,
) -> Path | None:
    import json
    items: List[PlannedItem] = []
    total_bytes = 0
    folder_decisions_all = {}
    for raw_entry in items_raw:
        if len(raw_entry) == 5:
            src, dest, size, category_path_raw, mime = raw_entry
            preview = None
            file_json_str = None
        elif len(raw_entry) == 6:
            src, dest, size, category_path_raw, mime, preview = raw_entry
            file_json_str = None
        else:
            src, dest, size, category_path_raw, mime, preview, file_json_str = raw_entry
        size_int = int(size or 0)
        total_bytes += size_int
        rule = _find_matching_rule(classifier, src, mime or "")
        category_path = CategoryPath(category_path_raw)
        items.append(
            PlannedItem(
                src=src,
                dest=dest,
                size=size_int,
                category_path=category_path,
                mime=mime,
                rule=rule,
                preview=(preview or ""),
            )
        )
        # Extract folder decisions from file_json
        if file_json_str:
            try:
                file_data = json.loads(file_json_str)
                if "folder_decisions" in file_data:
                    folder_decisions_all.update(file_data["folder_decisions"])
            except (json.JSONDecodeError, KeyError):
                pass

    if not items or total_bytes == 0:
        return None

    folder_batches: dict[tuple[str, str], dict[str, Any]] = {}
    handled_paths = set()

    for item in items:
        action = item.rule.folder_action if item.rule else "disaggregate"
        if action != "keep":
            continue
        src_dir = str(Path(item.src).parent)
        dest_dir = str(Path(item.dest).parent)
        src_folder_name = Path(src_dir).name
        dest_folder_name = Path(dest_dir).name
        
        # Only treat as folder copy if destination preserves source folder name
        # (i.e., /src/Folder/file -> /dest/Folder/file, not /src/Folder/file -> /dest/Category/file)
        if src_folder_name != dest_folder_name:
            continue
            
        key = (src_dir, dest_dir)
        if key not in folder_batches:
            folder_batches[key] = {
                "files": [],
                "bytes": 0,
                "rules": set(),
                "categories": Counter(),
            }
        entry: dict[str, Any] = folder_batches[key]
        entry["files"].append(item)
        entry["bytes"] += item.size
        entry["rules"].add(_normalise_rule(item.rule))
        entry["categories"][item.category_label] += 1
        handled_paths.add(item.src)

    file_groups: dict[tuple[str, str], list[PlannedItem]] = defaultdict(list)
    for item in items:
        if item.src in handled_paths:
            continue
        src_dir = str(Path(item.src).parent)
        dest_dir = str(Path(item.dest).parent)
        key = (src_dir, dest_dir)
        file_groups[key].append(item)

    batches: list[dict] = []

    for (src_dir, dest_dir), meta in folder_batches.items():
        batches.append(
            {
                "type": "folder",
                "source": src_dir,
                "dest": dest_dir,
                "files": meta["files"],
                "bytes": meta["bytes"],
                "rules": sorted(meta["rules"]),
                "categories": meta["categories"],
            }
        )

    CHUNK_BYTES = 5 * 1024 ** 3
    MAX_FILES = 200

    for (src_dir, dest_dir), entries in file_groups.items():
        entries.sort(key=lambda x: x.src)
        chunk: list[PlannedItem] = []
        chunk_bytes = 0
        for file_item in entries:
            chunk.append(file_item)
            chunk_bytes += file_item.size
            flush = chunk_bytes >= CHUNK_BYTES or len(chunk) >= MAX_FILES
            if flush:
                batches.append(
                    {
                        "type": "files",
                        "source": src_dir,
                        "dest": dest_dir,
                        "files": list(chunk),
                        "bytes": chunk_bytes,
                        "rules": sorted({_normalise_rule(it.rule) for it in chunk}),
                        "categories": Counter(it.category_label for it in chunk),
                    }
                )
                chunk = []
                chunk_bytes = 0
        if chunk:
            batches.append(
                {
                    "type": "files",
                    "source": src_dir,
                    "dest": dest_dir,
                    "files": list(chunk),
                    "bytes": chunk_bytes,
                    "rules": sorted({_normalise_rule(it.rule) for it in chunk}),
                    "categories": Counter(it.category_label for it in chunk),
                }
            )

    batches = [b for b in batches if b["bytes"] > 0]
    batches.sort(key=lambda b: b["bytes"], reverse=True)

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated copy plan. Commands are safe to re-run; rsync handles resumable transfers.",
        f"# Total planned copy size: {_format_bytes(total_bytes)} ({_format_gib(total_bytes)})",
        "",
    ]
    
    # Add folder decisions summary if available
    if folder_decisions_all:
        lines.append("# Folder AI Decisions (sample):")
        for path, decision in sorted(folder_decisions_all.items())[:20]:
            lines.append(f"#   {path}: {decision}")
        if len(folder_decisions_all) > 20:
            lines.append(f"#   ... ({len(folder_decisions_all) - 20} more folders)")
        lines.append("")

    for batch in batches:
        batch_bytes = batch["bytes"]
        pct = _percent(batch_bytes, total_bytes)
        rules = batch["rules"] or ["<none>"]
        categories: Counter = batch["categories"]
        category_line = ", ".join(
            f"{label} x{count}" for label, count in categories.most_common()
        ) or "Unknown"
        lines.append("# ------------------------------------------------------------")
        if batch["type"] == "folder":
            src_dir = batch["source"]
            dest_dir = batch["dest"]
            base_path = Path(src_dir)
            rel_files_list: List[str] = []
            for it in batch["files"]:
                src_path = Path(it.src)
                try:
                    rel = src_path.relative_to(base_path)
                    rel_files_list.append(str(rel))
                except ValueError:
                    rel_files_list.append(src_path.name)
            rel_files_list.sort()
            rel_display = ", ".join(rel_files_list[:10])
            if len(rel_files_list) > 10:
                rel_display += f", ... ({len(rel_files_list)} files)"
            lines.append(f"# Folder copy: {src_dir} -> {dest_dir}")
            lines.append(f"# Files sample: {rel_display}")
            # Add folder decisions for this source folder
            if src_dir in folder_decisions_all:
                lines.append(f"# Folder decision: {folder_decisions_all[src_dir]}")
        else:
            src_dir = batch["source"]
            dest_dir = batch["dest"]
            file_list = ", ".join(Path(it.src).name for it in batch["files"][:10])
            if len(batch["files"]) > 10:
                file_list += f", ... ({len(batch['files'])} files)"
            lines.append(f"# Files: {src_dir} -> {dest_dir}")
            lines.append(f"# Files sample: {file_list}")
        lines.append(f"# Categories: {category_line if category_line else 'Unknown'}")
        lines.append(f"# Rules: {', '.join(rules)}")
        preview_lines = []
        for item in batch["files"]:
            if getattr(item, "preview", None):
                safe_preview = item.preview[:120].replace('\n', ' ').replace('\r', ' ')
                filename = Path(item.src).name
                preview_lines.append(f"#   {filename}: {safe_preview}")
        if preview_lines:
            lines.append("# Previews:")
            lines.extend(preview_lines[:10])
            if len(preview_lines) > 10:
                lines.append(f"#   ... ({len(preview_lines) - 10} more files)")
        lines.append(f"# Batch size: {_format_gib(batch_bytes)} ({_format_bytes(batch_bytes)}) [{pct}% of total]")
        lines.append("")
        if batch["type"] == "folder":
            src_dir = batch["source"]
            dest_dir = batch["dest"]
            lines.append(f"mkdir -p {_quote(dest_dir)}")
            lines.append(
                "rsync -a --partial --info=progress2 --human-readable --append-verify "
                f"{_quote(_with_trailing_slash(src_dir))} {_quote(_with_trailing_slash(dest_dir))}"
            )
        else:
            src_dir = batch["source"]
            dest_dir = batch["dest"]
            lines.append(f"SRC={_quote(src_dir)}")
            lines.append(f"DEST={_quote(dest_dir)}")
            lines.append(f"mkdir -p \"$DEST\"")
            for item in batch["files"]:
                filename = Path(item.src).name
                lines.append(f"rsync -a --partial --append-verify \"$SRC\"/{_quote(filename)} \"$DEST\"/")

        lines.append("")  # blank line after each command set

    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("\n".join(lines), encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path
def _with_trailing_slash(path: str) -> str:
    return path if path.endswith("/") else path + "/"
