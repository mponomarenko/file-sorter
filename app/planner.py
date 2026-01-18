import os
import asyncio
import pathlib
import subprocess
from datetime import datetime, timezone
from typing import Optional
import re

from .categories import CategoryPath, UNKNOWN_CATEGORY
from .utils import log
from .db import Database
from .media import MediaHelper, peek_text
from .classifiers import (
    Classifier,
    ClassifierResponse,
    RulesClassifier,
    OllamaClassifier,
)
from .config import AppConfig
from .folder_action import RequiresAI, FolderAction
from .metadata import collect_file_metadata
from .file_metadata import FileMetadata
from .folder_policy import FolderSample, collect_folder_samples, build_folder_action_map
from .file_nodes import FileNodeBuilder
from .classification_records import ClassificationRecordBuilder


class Planner:
    def __init__(self, cfg: AppConfig, db: Database, media: MediaHelper):
        self.cfg = cfg
        self.db = db
        self.media = media
        self.classifier_factory = OllamaClassifier
        self.peek_text_fn = peek_text
        self.rules_engine = RulesClassifier()
        self.record_builder = ClassificationRecordBuilder(cfg)
        wrapper_pattern = cfg.SOURCE_WRAPPER_REGEX or ""
        self._source_wrapper_pattern = re.compile(wrapper_pattern, re.IGNORECASE) if wrapper_pattern else None

    def _ensure_classifier(self, current: Optional[Classifier]) -> Classifier:
        if current is not None:
            return current
        if getattr(self.cfg, "CLASSIFIER_KIND", "").lower() == "manual":
            return self.rules_engine
        endpoints = self.cfg.ollama_endpoints()
        if endpoints:
            url, workers, model = endpoints[0]
            return self.classifier_factory(url=url, model=model, max_concurrency=workers)
        return self.rules_engine

    def _rel_for_classifier(self, path_obj: pathlib.Path) -> str:
        path_str = str(path_obj)
        norm_path = os.path.normcase(os.path.normpath(path_str))
        best_root: Optional[str] = None
        best_len = -1
        for raw_root in self.cfg.SOURCES:
            root = (raw_root or "").strip()
            if not root:
                continue
            norm_root = os.path.normcase(os.path.normpath(root))
            if norm_path == norm_root:
                best_root = root
                best_len = len(norm_root)
                break
            prefix = norm_root + os.sep
            if norm_path.startswith(prefix) and len(norm_root) > best_len:
                best_root = root
                best_len = len(norm_root)
        if best_root:
            rel = os.path.relpath(path_str, best_root)
            if rel == ".":
                rel = path_obj.name
            parts = [p for p in rel.replace("\\", "/").split("/") if p]
            if parts and self._source_wrapper_pattern and self._source_wrapper_pattern.fullmatch(parts[0]):
                parts = parts[1:]
            return "/".join(parts) if parts else path_obj.name
        parent_name = path_obj.parent.name
        if parent_name:
            return f"{parent_name}/{path_obj.name}"
        return path_obj.name

    async def _resolve_folder_actions(
        self,
        samples: dict[str, FolderSample],
        classifier: Classifier | None,
    ) -> tuple[dict[str, FolderAction], dict[str, str]]:
        """Resolve folder actions using the centralized function.
        
        Returns: (actions_map, decisions_map)
        """
        if not samples:
            return {}, {}
        loop = asyncio.get_running_loop()
        advisor = classifier if classifier and getattr(classifier, "is_ai", lambda: False)() else None
        return await loop.run_in_executor(
            None,  # default thread pool
            lambda: build_folder_action_map(
                self.rules_engine,
                advisor,
                samples,
                self.cfg.SOURCES,
                self.cfg.SOURCE_WRAPPER_REGEX,
            ),
        )

    def _get_persisted_decisions(self) -> dict[str, str]:
        """Get folder decision sources from database."""
        with self.db.connect() as con:
            cur = con.cursor()
            rows = cur.execute("SELECT folder_path, decision_source FROM folder_actions").fetchall()
            return {folder_path: decision_source for folder_path, decision_source in rows if decision_source}

    async def classify_and_plan(self, batch_size: int = 1000, classifier: Classifier | None = None):
        pool: Classifier | None = classifier

        try:
            while True:
                rows = self.db.select_unclassified(batch_size)

                if not rows:
                    log.info("No files to classify.")
                    break

                total = len(rows)
                log.info(f"Classifying {total:,} files, first: {rows[0]}...")
                log.debug(f"classify: processing batch of {len(rows)} files")

                pool = self._ensure_classifier(pool)
                folder_samples = collect_folder_samples(rows)

                async def one(index: int, total_files: int, path: str, mime: str, size: int = 0):
                    p = pathlib.Path(path)
                    rel = self._rel_for_classifier(p)
                    rule_match = self.rules_engine.match(p.name, rel, mime)
                    rule = rule_match.rule if rule_match else None
                    rule_hint = str(rule.category_path) if rule else "Unknown"
                    log.debug(f"classify_debug file={path} mime={mime} rule_hint={rule_hint}")

                    response: ClassifierResponse
                    origin = None
                    sample = None
                    rule_action = rule.folder_action if rule else None
                    metadata: FileMetadata = collect_file_metadata(path, mime)
                    if rule_match:
                        for key, value in rule_match.named_groups().items():
                            metadata.add(key, value)
                    if rule and (rule.requires_ai == RequiresAI.FINAL or not pool.is_ai()):
                        metrics = {
                            "source": "rules",
                            "rule_match": True,
                            "requires_ai": str(rule.requires_ai),
                            "folder_action": str(rule.folder_action) if rule.folder_action else None,
                        }
                        response = ClassifierResponse(path=rule.category_path, metrics=metrics)
                        origin = "rules"
                    else:
                        hint: dict[str, object] = {
                            "source_path": path,
                            "metadata": metadata.to_dict(),
                        }
                        if rule and rule.requires_ai == RequiresAI.AI:
                            hint["rule_category_path"] = str(rule.category_path)
                            hint["rule"] = {
                                "path_pattern": rule.path_pattern or "*",
                                "mime_pattern": rule.mime_pattern or "*",
                                "folder_action": str(rule.folder_action) if rule.folder_action else "",
                                "requires_ai": str(rule.requires_ai),
                            }
                            hint["rule_hint"] = rule.category_path
                        sample = self.peek_text_fn(path, mime, self.cfg.MAX_CONTENT_PEEK)
                        log.debug(
                            f"classifier_debug request name={p.name} rel={rel} mime={mime} sample_len={len(sample)}"
                        )
                        response = await pool.classify(p.name, rel, mime, sample, hint)
                        origin = pool.display_name()
                        log.debug(f"classifier_debug result file={path} -> {response.path}")
                        if response.failed:
                            log.warning(
                                "classifier_failure",
                                file=path,
                                worker=origin,
                                error=str(response.error),
                                context=response.error_context or {},
                            )
                    category_path = response.path or UNKNOWN_CATEGORY
                    if not isinstance(category_path, CategoryPath):
                        category_path = CategoryPath(category_path)
                    metadata.update(response.metadata())
                    return (
                        path,
                        mime,
                        rel,
                        response,
                        origin,
                        sample,
                        index,
                        total_files,
                        rule_action,
                        metadata,
                        rule_match,
                    )

                results = await asyncio.gather(*(one(i + 1, total, p, m, s) for i, (p, m, s) in enumerate(rows)))
                
                # Load existing folder actions from database
                persisted_actions = self.db.get_folder_actions()
                
                # Resolve folder actions for new folders in this batch
                new_folder_action_map, new_folder_decisions = await self._resolve_folder_actions(folder_samples, pool)
                
                # Save new folder actions to database
                if new_folder_action_map:
                    self.db.save_folder_actions(
                        {k: v.value for k, v in new_folder_action_map.items()},
                        new_folder_decisions
                    )
                
                # Merge with persisted actions (persisted takes precedence)
                from .folder_action import FolderAction
                merged_action_map = {}
                for folder, action_str in persisted_actions.items():
                    try:
                        merged_action_map[folder] = FolderAction.from_string(action_str)
                    except ValueError:
                        pass
                merged_action_map.update(new_folder_action_map)
                
                node_builder = FileNodeBuilder(
                    sources=self.cfg.SOURCES,
                    folder_action_map=merged_action_map,
                    folder_decisions={**new_folder_decisions, **self._get_persisted_decisions()},
                    source_wrapper_pattern=self.cfg.SOURCE_WRAPPER_REGEX,
                )

                updates = []
                folder_stats: dict[str, dict[str, int]] = {}
                for (
                    path,
                    mime,
                    _rel,
                    response,
                    origin,
                    sample,
                    index,
                    total_files,
                    rule_action,
                    metadata,
                    rule_match,
                ) in results:
                    category_path = response.path or UNKNOWN_CATEGORY
                    if not isinstance(category_path, CategoryPath):
                        category_path = CategoryPath(category_path)
                    metadata.update(response.metadata())
                    rule_category = rule_match.rule.category_path if rule_match and rule_match.rule else None
                    ai_category = None
                    if origin and origin != "rules":
                        ai_category = category_path
                    file_node = node_builder.build(
                        path,
                        category=category_path,
                        rule_category=rule_category,
                        ai_category=ai_category,
                        mime=mime,
                        metadata=metadata,
                        rule_match=rule_match,
                        classifier_origin=origin,
                        preview=sample,
                    )
                    classified_path = self.media.build_destination(file_node)
                    updates.append(self.record_builder.build(file_node, classified_path))
                    log.debug(
                        f"plan_debug {path} -> {classified_path.destination} [{category_path}] layers={classified_path.explanation()}"
                    )
                    remaining = total_files - index
                    percent = (index / total_files * 100.0) if total_files else 100.0
                    metrics = response.metrics or {}
                    log_payload = dict(
                        file=path,
                        category=category_path.label,
                        destination=classified_path.destination,
                        source=origin,
                        index=index,
                        total=total_files,
                        remaining=remaining,
                        percent=f"{percent:.1f}",
                        success=not response.failed,
                        metrics=metrics,
                    )
                    if response.error:
                        log_payload["error"] = str(response.error)
                    if response.error_context:
                        log_payload["error_context"] = response.error_context
                    log.info(
                        "classification",
                        **log_payload,
                    )
                    folder = str(pathlib.Path(path).parent)
                    stats = folder_stats.setdefault(folder, {"total": 0, "keep": 0})
                    stats["total"] += 1
                    if rule_action == "keep":
                        stats["keep"] += 1

                if updates:
                    log.debug(f"classify: updating DB with {len(updates)} planned rows")
                    self.db.update_category_dest(updates)

                for folder, stats in folder_stats.items():
                    if stats["keep"]:
                        reason = "all_keep" if stats["keep"] == stats["total"] else "partial_keep"
                        log.info(
                            "folder_action",
                            folder=folder,
                            action="move_as_unit",
                            reason=reason,
                            keep=stats["keep"],
                            total=stats["total"],
                        )

        finally:
            if pool is not None:
                await pool.close()
