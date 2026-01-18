import os
import sys
import time
import pathlib
import logging
from concurrent.futures import as_completed
from typing import Optional
import asyncio

from .utils import log, scan_pool
from .config import AppConfig
from .db import Database
from .media import MediaHelper, detect_mime, blake3_hash
from .planner import Planner
from .folders import FolderAnalyzer
from .copy_plan import build_copy_script
from .classifiers import (
    Classifier,
    OllamaClassifier,
    MultiplexedClassifier,
    RulesClassifier,
    create_ai_classifier,
)


class Orchestrator:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        database: Database | None = None,
        media: MediaHelper | None = None,
        planner: Planner | None = None,
        folders: FolderAnalyzer | None = None,
        mover: object | None = None,
    ):
        self.cfg = cfg
        self.db = database or Database(cfg)
        self.media = media or MediaHelper(cfg)
        self.planner = planner or Planner(cfg, self.db, self.media)
        self.folders = folders or FolderAnalyzer(cfg, self.db)
        self.mover = mover

    def _choose_classifier(self) -> Classifier | None:
        if self.cfg.CLASSIFIER_KIND == "manual":
            return RulesClassifier()
        endpoints = self.cfg.ollama_endpoints()
        classifiers = []
        for url, workers, model in endpoints:
            try:
                classifier = create_ai_classifier(url=url, model=model, max_concurrency=workers)
                if classifier and classifier.ensure_available():
                    classifiers.append(classifier)
                else:
                    log.warning(f"Classifier not available for {url}")
            except Exception as e:
                log.warning(f"Failed to create classifier for {url}: {e}")
        if not classifiers:
            return None
        if len(classifiers) == 1:
            return classifiers[0]
        return MultiplexedClassifier(classifiers)

    def scan_paths(self) -> None:
        log.debug("scan: start")
        
        # Count existing files in DB before scanning
        files_before = self.db.count_all_files()
        log.info(f"Files already in database: {files_before:,}")
        
        to_stat = []
        for root in self.cfg.SOURCES:
            if not root or not os.path.isdir(root):
                continue
            for dirpath, _, filenames in os.walk(root):
                for fn in filenames:
                    to_stat.append(os.path.join(dirpath, fn))
        log.info(f"Discovered {len(to_stat):,} files to scan")

        def stat_mime(path):
            try:
                st = os.stat(path, follow_symlinks=False)
                mime = detect_mime(path)
                log.debug(f"scan: {path} size={st.st_size} mime={mime}")
                return (path, st.st_size, st.st_mtime, mime, None, "scanned")
            except Exception as e:
                log.warning(f"Stat/mime error: {path} : {e}")
                return None

        rows = []
        total_inserted = 0
        futures = [scan_pool.submit(stat_mime, p) for p in to_stat]
        for fut in as_completed(futures):
            res = fut.result()
            if res is None:
                continue
            rows.append(res)
            if len(rows) >= self.cfg.DB_BATCH_SIZE:
                inserted = self.db.bulk_insert(
                    [
                        (p, s, m, mi, blake3_hash(p) if s > 0 else "", st)
                        for (p, s, m, mi, _, st) in rows
                    ]
                )
                total_inserted += inserted
                rows.clear()
        if rows:
            inserted = self.db.bulk_insert(
                [
                    (p, s, m, mi, blake3_hash(p) if s > 0 else "", st)
                    for (p, s, m, mi, _, st) in rows
                ]
            )
            total_inserted += inserted

        files_after = self.db.count_all_files()
        log.info(f"Scan complete: {total_inserted:,} new files added, {files_after:,} total files in database")

        self.folders.compute_folder_hashes()

    def write_report(self) -> None:
        log.debug("report: start")
        pathlib.Path(self.cfg.REPORT_DIR).mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        out_files = pathlib.Path(self.cfg.REPORT_DIR) / f"cleanup_report_{ts}.csv"
        with open(out_files, "w", encoding="utf-8") as f:
            f.write(
                "path|size|mime|hash|category|rule_category|ai_category|dest|status|note|metadata_json\n"
            )
            for (
                path,
                size,
                mime,
                hash_value,
                category,
                dest,
                rule_category,
                ai_category,
                metadata_json,
                _preview,
                _file_json,
                status,
                note,
            ) in self.db.iter_all():
                f.write(
                    "|".join(
                        [
                            str(path or ""),
                            str(size or ""),
                            str(mime or ""),
                            str(hash_value or ""),
                            str(category or ""),
                            str(rule_category or ""),
                            str(ai_category or ""),
                            str(dest or ""),
                            str(status or ""),
                            str(note or ""),
                            str(metadata_json or ""),
                        ]
                    )
                    + "\n"
                )
        log.info(f"Report (files): {out_files}")

        groups = self.folders.find_duplicate_folders()
        out_folders = pathlib.Path(self.cfg.REPORT_DIR) / f"duplicate_folders_{ts}.csv"
        with open(out_folders, "w", encoding="utf-8") as f:
            f.write("folder_hash|group_size|total_bytes|paths\n")
            for g in groups:
                paths_joined = "\x1f".join(g.get("paths", []))
                f.write("|".join([
                    str(g.get("hash", "")),
                    str(len(g.get("paths", []))),
                    str(g.get("size", 0)),
                    paths_joined,
                ]) + "\n")
        log.info(f"Report (folders): {out_folders}")

        planned = self.db.select_planned_details()
        if planned:
            rules = RulesClassifier()
            if rules.ensure_available():
                script_path = pathlib.Path(self.cfg.REPORT_DIR) / f"copy_plan_{ts}.sh"
                result = build_copy_script(planned, rules, script_path)
                if result:
                    log.info(f"Copy script: {result}")
            else:
                log.warning("copy_script_skipped", reason="rules_unavailable")
        else:
            log.info("No planned copies; skipping copy script generation")

    async def _classify_and_plan(self, classifier: Classifier | None) -> None:
        await self.planner.classify_and_plan(classifier=classifier)

    def main(self, mode: str) -> int:
        log.info(f"Starting in mode: {mode}")
        classifier: Classifier | None = None
        if mode in ("classify", "all"):
            classifier = self._choose_classifier()
            if classifier and not classifier.ensure_available():
                log.error("Aborting: Classifier not available.")
                return 1
            if classifier:
                log.info("classifier_ready", name=classifier.display_name())
        if mode in ("scan", "all"):
            self.scan_paths()
        if mode in ("classify", "all"):
            asyncio.run(self._classify_and_plan(classifier))
        if mode in ("move", "all"):
            if self.mover is None:
                log.warning("Move step requested, but mover is disabled; skipping.")
            else:
                self.mover.move_files()  # type: ignore[attr-defined]
        if mode in ("report", "all"):
            self.write_report()
        log.info("Done.")
        return 0


def run_cli() -> int:
    cfg = AppConfig.from_env()
    orchestrator = Orchestrator(cfg)
    mode = (sys.argv[1] if len(sys.argv) > 1 else cfg.MODE).lower()
    return orchestrator.main(mode)


if __name__ == "__main__":
    sys.exit(run_cli())
