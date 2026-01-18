import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Set, Any
from blake3 import blake3

from .utils import log
from .config import AppConfig
from .exceptions import FileOperationError
from .db import Database


def _hash_folder_text(lines: List[str]) -> str:
    """Hash the canonical text representation for a folder."""
    h = blake3()
    for line in lines:
        h.update((line + "\n").encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _all_ancestors(p: Path) -> List[Path]:
    """Return all directory ancestors including the immediate parent."""
    if not isinstance(p, Path):
        p = Path(p)
    if not p.is_absolute():
        try:
            p = p.resolve()
        except Exception:
            pass

    ancestors: List[Path] = []
    seen: Set[str] = set()
    current = p.parent
    while current and str(current) not in ("", "/", "."):
        str_current = str(current)
        if str_current in seen:
            break
        ancestors.append(current)
        seen.add(str_current)
        current = current.parent
    return ancestors


class FolderAnalyzer:
    def __init__(self, cfg: AppConfig, db: Database):
        self.cfg = cfg
        self.db = db

    def compute_folder_hashes(self, batch_size: int = 5000) -> None:
        log.info("building_folder_hashes", batch_size=batch_size)
        paths_map: Dict[str, List[Tuple[str, str, int]]] = defaultdict(list)

        for path, fhash, size in self.db.iter_all_files_for_folder_hashing():
            try:
                file_path = Path(path)
                for anc in _all_ancestors(file_path):
                    try:
                        rel = str(file_path.relative_to(anc))
                        base = str(anc)
                        paths_map[base].append((rel, fhash or "", int(size or 0)))
                    except ValueError as exc:
                        log.warning(
                            "relative_path_error",
                            path=path,
                            ancestor=str(anc),
                            error=str(exc),
                        )
                        continue
            except Exception as exc:
                log.warning("invalid_path_error", path=path, error=str(exc))
                continue

        if not paths_map:
            log.info("no_folder_structures")
            return

        rows = []
        total_folders = len(paths_map)
        processed = 0

        for folder, items in paths_map.items():
            items.sort(key=lambda t: t[0])
            lines = [f"{rel}|{fh}" for (rel, fh, _) in items]
            fh = _hash_folder_text(lines)
            file_count = len(items)
            byte_size = sum(sz for (_, _, sz) in items)
            rows.append((folder, fh, file_count, byte_size))
            processed += 1

            if len(rows) >= batch_size:
                self.db.upsert_folder_hashes(rows)
                log.debug(
                    "folder_hash_batch_complete",
                    processed=processed,
                    total=total_folders,
                    batch_size=len(rows),
                )
                rows.clear()

        if rows:
            self.db.upsert_folder_hashes(rows)
            log.debug(
                "folder_hash_complete",
                processed=processed,
                total=total_folders,
                final_batch=len(rows),
            )

    def find_duplicate_folders(self) -> List[Dict[str, Any]]:
        groups = self.db.select_duplicate_folders()
        size_map: Dict[str, int] = {}

        with self.db.connect() as con:
            cur = con.cursor()
            cur.execute(
                "SELECT folder_hash, SUM(byte_size) FROM folder_hashes GROUP BY folder_hash"
            )
            for h, sz in cur.fetchall():
                size_map[h] = int(sz or 0)

        out = [
            {"hash": h, "paths": paths, "size": size_map.get(h, 0)}
            for (h, paths) in groups
        ]
        out.sort(key=lambda g: int(g.get("size", 0) or 0), reverse=True)  # type: ignore[call-overload]

        log.debug(
            "duplicate_folders_found",
            group_count=len(out),
            total_size=sum(int(g.get("size", 0) or 0) for g in out),  # type: ignore[call-overload]
        )
        return out
