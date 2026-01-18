import csv
import re
from pathlib import Path
from typing import Optional

from ..categories import CategoryPath, UNKNOWN_CATEGORY
from ..config import config
from ..utils import log
from ..rules_models import CompiledRule, RuleMatch
from ..folder_action import FolderAction, RequiresAI, FolderActionRequest
from .base import Classifier, ClassifierResponse, FolderActionResponse


class RulesClassifier(Classifier):
    def __init__(self, rules_path: str | Path | None = None):
        default_path = Path(__file__).resolve().parents[1] / "data" / "rules.csv"
        self.rules_path = Path(rules_path) if rules_path else default_path
        self.rules: list[CompiledRule] = []
        self._load_errors: list[str] = []
        self.categories = config.categories
        self._load_rules()

    def _load_rules(self) -> None:
        processed_lines: list[tuple[int, str]] = []
        try:
            with self.rules_path.open("r", encoding="utf-8") as f:
                for idx, raw in enumerate(f, start=1):
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "#" in line:
                        line = line.split("#", 1)[0].strip()
                        if not line:
                            continue
                    processed_lines.append((idx, line))
        except FileNotFoundError:
            self._log_error(0, f"Rules file not found: {self.rules_path}")
            return

        for line_no, line in processed_lines:
            try:
                row = next(csv.reader([line]))
            except Exception as exc:
                self._log_error(line_no, f"CSV parse error: {exc}", line)
                continue

            if not row:
                continue
            while len(row) < 5:
                row.append("")
            path_pat, mime_pat, category_raw, action, ai_flag = [item.strip() for item in row[:5]]
            if not category_raw:
                self._log_error(line_no, "Missing category", line)
                continue

            try:
                path_pat_norm = None if path_pat in ("", "*") else path_pat
                mime_pat_norm = None if mime_pat in ("", "*") else mime_pat
                path_regex = re.compile(path_pat_norm, re.IGNORECASE) if path_pat_norm else None
            except re.error as exc:
                self._log_error(line_no, f"Invalid path regex '{path_pat}': {exc}", line)
                continue

            try:
                mime_regex = re.compile(mime_pat_norm, re.IGNORECASE) if mime_pat_norm else None
            except re.error as exc:
                self._log_error(line_no, f"Invalid mime regex '{mime_pat}': {exc}", line)
                continue

            # Parse folder action using enum
            folder_action: FolderAction | None = None
            if action:
                try:
                    folder_action = FolderAction.from_string(action)
                except ValueError as exc:
                    self._log_error(line_no, str(exc), line)
                    continue

            # Parse requires_ai using enum
            ai_flag_norm = (ai_flag or "final").strip().lower()
            if not ai_flag_norm:
                ai_flag_norm = "final"
            try:
                requires_ai_enum = RequiresAI.from_string(ai_flag_norm)
            except ValueError as exc:
                self._log_error(line_no, str(exc), line)
                continue

            category_path = self.categories.normalize(category_raw)
            if category_path is None:
                self._log_error(
                    line_no,
                    f"Unknown category path '{category_raw}'",
                    line,
                )
                continue

            self.rules.append(
                CompiledRule(
                    path_pattern=path_pat_norm,
                    mime_pattern=mime_pat_norm,
                    path_regex=path_regex,
                    mime_regex=mime_regex,
                    category_path=category_path,
                    folder_action=folder_action,
                    requires_ai=requires_ai_enum,
                    line_number=line_no,
                )
            )

    async def classify(self, name: str, rel_path: str, mime: str, sample: str, hint: dict | None = None) -> ClassifierResponse:
        key = rel_path or name or ""
        match = self._match_rule(key, mime)
        path = match.rule.category_path if match else UNKNOWN_CATEGORY
        
        metrics = {
            "source": "rules",
            "rule_match": bool(match),
            "rule_pattern": match.rule.path_pattern if match else None,
            "rule_mime": match.rule.mime_pattern if match else None
        }
        return ClassifierResponse(path, metrics)

    async def close(self):
        return None

    def ensure_available(self) -> bool:
        return not self._load_errors

    def display_name(self) -> str:
        return f"rules({self.rules_path})"

    def is_ai(self) -> bool:
        return False

    def _match_rule(self, rel_path: str, mime: str) -> RuleMatch | None:
        raw = rel_path or ""
        if not raw.startswith("/"):
            raw = "/" + raw
        for rule in self.rules:
            result = rule.match(raw, mime or "")
            if result is not None:
                path_match, mime_match = result
                return RuleMatch(rule=rule, path_match=path_match, mime_match=mime_match)
        return None

    def match(self, name: str, rel_path: str, mime: str) -> RuleMatch | None:
        key = rel_path or name or ""
        return self._match_rule(key, mime)

    def advise_folder_action(self, request: FolderActionRequest) -> FolderActionResponse:
        """Determine folder action based on rules.
        
        Returns:
            FolderActionResponse with:
            - decision: if rule says final (requires_ai=final) or keep_parent marker found
            - delegation with hint: if rule says requires_ai=ai and provides folder_action
            - delegation without hint: if no rules match
        """
        
        # Check for keep_parent markers in children - these are FINAL decisions
        for child in request.children:
            child_name = child.get("name", "")
            child_type = child.get("type", "")
            child_path = f"{request.folder_path.rstrip('/')}/{child_name}"
            
            if child_type == "dir":
                dir_match = self.match(child_name, child_path, "*")
                inside_match = self.match("", f"{child_path}/", "*")
                
                if (dir_match and dir_match.rule.folder_action == FolderAction.KEEP_PARENT) or \
                   (inside_match and inside_match.rule.folder_action == FolderAction.KEEP_PARENT):
                    log.debug("rules_keep_parent_marker", folder=request.folder_path, marker=child_name)
                    return FolderActionResponse.decision(FolderAction.KEEP, reason=f"keep_parent:{child_name}")
            
            elif child_type == "file":
                file_match = self.match(child_name, child_path, child.get("mime", "*"))
                
                if not file_match:
                    continue
                
                if file_match.rule.folder_action == FolderAction.KEEP_PARENT:
                    log.debug("rules_keep_parent_marker", folder=request.folder_path, marker=child_name)
                    return FolderActionResponse.decision(FolderAction.KEEP, reason=f"keep_parent:{child_name}")
                
                if file_match.rule.folder_action and file_match.rule.requires_ai == RequiresAI.FINAL:
                    log.debug("rules_final_action", folder=request.folder_path, action=file_match.rule.folder_action)
                    return FolderActionResponse.decision(file_match.rule.folder_action, reason="rule:final")
                
                if file_match.rule.requires_ai == RequiresAI.AI:
                    log.debug("rules_delegates_with_hint", folder=request.folder_path, hint=file_match.rule.folder_action)
                    return FolderActionResponse.delegate(hint=file_match.rule.folder_action, reason="rule:requires_ai")
        
        # Check explicit folder rule
        folder_match = self.match("", request.folder_path, "") or self.match("", f"{request.folder_path}/", "")
        if folder_match:
            if folder_match.rule.folder_action and folder_match.rule.requires_ai == RequiresAI.FINAL:
                log.debug("rules_folder_final", folder=request.folder_path, action=folder_match.rule.folder_action)
                return FolderActionResponse.decision(folder_match.rule.folder_action, reason="rule:folder:final")
            
            if folder_match.rule.requires_ai == RequiresAI.AI:
                log.debug("rules_folder_delegates", folder=request.folder_path, hint=folder_match.rule.folder_action)
                return FolderActionResponse.delegate(hint=folder_match.rule.folder_action, reason="rule:folder:requires_ai")
        
        # No rules matched - delegate with safe default hint
        log.debug("rules_no_match_delegate", folder=request.folder_path)
        return FolderActionResponse.delegate(hint=FolderAction.DISAGGREGATE, reason="rule:no_match")

    def _log_error(self, line: int, message: str, raw: str | None = None) -> None:
        entry = f"{self.rules_path}:{line}: {message}"
        if raw is not None:
            entry += f" | {raw}"
        self._load_errors.append(entry)
        log.error(
            "rules_classifier_rule_error",
            file=str(self.rules_path),
            line=line,
            message=message,
            raw=raw or "",
        )
