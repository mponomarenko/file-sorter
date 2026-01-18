#!/usr/bin/env python3
import os
import sys
import json
import asyncio
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path for importing app modules
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.categories import CategoryPath
from app.classifiers import RulesClassifier, ClassifierResponse, Classifier
from app.classifiers.ai_auto import create_ai_classifier
from app.media import MediaHelper, detect_mime, peek_text
from app.metadata import collect_file_metadata
from app.folder_policy import collect_folder_samples, build_folder_action_map
from app.file_nodes import FileNode, FileNodeBuilder
from app.path_models import ClassifiedPath
from app.db import Database
from app.classification_records import ClassificationRecord, ClassificationRecordBuilder


@dataclass
class AIWorker:
    name: str
    classifier: Classifier


@dataclass
class AIWorkerResult:
    worker: str
    ttft: Optional[float]
    total: float
    response: Optional[ClassifierResponse]
    error: Optional[str]
    timed_out: bool
    metrics: Dict[str, Any]


def _normalize_url(raw: str) -> str:
    value = str(raw).strip()
    if not value.startswith(("http://", "https://")):
        return f"http://{value}"
    return value


def _read_prompt_template(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    prompt_path = Path(path).expanduser()
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")
    content = prompt_path.read_text(encoding="utf-8").strip()
    return content or None


async def _init_ai_workers(
    cfg: AppConfig,
    override_url: Optional[str],
    prompt_template: Optional[str],
) -> tuple[List[AIWorker], List[Classifier]]:
    endpoints: List[tuple[str, int, str]]
    if override_url:
        # Parse override URL in format: url|workers|model
        raw_entries = [entry.strip() for entry in override_url.split(",") if entry.strip()]
        parsed_endpoints = []
        for entry in raw_entries:
            parts = entry.split("|")
            url = parts[0].strip()
            workers_count = int(parts[1]) if len(parts) > 1 else 1
            model = parts[2].strip() if len(parts) > 2 else None
            if not model:
                raise ValueError(f"Model required in URL format: {entry} (use url|workers|model)")
            parsed_endpoints.append((url, workers_count, model))
        endpoints = parsed_endpoints
    else:
        endpoints = cfg.ollama_endpoints()
    workers: List[AIWorker] = []
    classifiers: List[Classifier] = []
    for url, slots, model in endpoints:
        normalized = _normalize_url(url)
        try:
            classifier = create_ai_classifier(
                url=normalized,
                model=model,
                max_concurrency=1,
                folder_prompt_template=prompt_template,
            )
            if not classifier.ensure_available():
                print(f"Worker unavailable: {normalized} (model={model})", file=sys.stderr)
                await classifier.close()
                continue
        except Exception as exc:
            print(f"Worker check failed for {normalized} (model={model}): {exc}", file=sys.stderr)
            continue
        if slots and slots > 1:
            print(f"CLI throttling {normalized} to a single worker (configured slots={slots})", file=sys.stderr)
        classifiers.append(classifier)
        workers.append(AIWorker(name=f"{normalized}({model})", classifier=classifier))
    return workers, classifiers


async def _classify_with_worker(worker: AIWorker, name: str, rel_path: str, mime: str,
                                sample: str, hint: Optional[Dict[str, Any]], timeout: int) -> AIWorkerResult:
    loop = asyncio.get_running_loop()
    started = loop.time()
    metrics: Dict[str, Any] = {}
    try:
        print(f"-> dispatching {worker.name}", file=sys.stderr)
        response = await asyncio.wait_for(worker.classifier.classify(name, rel_path, mime, sample, hint), timeout=timeout)
        total = loop.time() - started
        metrics = response.metrics.copy() if response.metrics else {}
        ttft = metrics.get("ttft") or metrics.get("total_duration") or total
        return AIWorkerResult(worker=worker.name, ttft=ttft, total=total, response=response, error=None, timed_out=False, metrics=metrics)
    except asyncio.TimeoutError:
        total = loop.time() - started
        return AIWorkerResult(worker=worker.name, ttft=None, total=total, response=None, error=f"timeout after {timeout}s", timed_out=True, metrics={"total_duration": total})
    except Exception as exc:
        total = loop.time() - started
        if not metrics:
            metrics["total_duration"] = total
        return AIWorkerResult(worker=worker.name, ttft=None, total=total, response=None, error=str(exc), timed_out=False, metrics=metrics)


def _result_to_dict(result: AIWorkerResult) -> Dict[str, Any]:
    output: Dict[str, Any] = {
        "worker": result.worker,
        "ttft": result.ttft,
        "total_duration": result.total,
        "timed_out": result.timed_out,
        "metrics": result.metrics,
    }
    if result.response:
        output["category"] = str(result.response.path)
        output["success"] = not result.response.failed
        if result.response.error:
            output["error"] = str(result.response.error)
            output["error_context"] = result.response.error_context
    else:
        output["success"] = False
        if result.error:
            output["error"] = result.error
    return output


async def _classify_all_workers(workers: List[AIWorker], name: str, rel_path: str, mime: str,
                                sample: str, hint: Optional[Dict[str, Any]], timeout: int) -> tuple[Dict[str, Any], Optional[ClassifierResponse]]:
    if not workers:
        return {"best": None, "workers": []}, None
    tasks = [
        _classify_with_worker(worker, name, rel_path, mime, sample, hint, timeout)
        for worker in workers
    ]
    results = await asyncio.gather(*tasks)
    best: Optional[AIWorkerResult] = None
    for result in results:
        if result.response and not result.response.failed:
            current = result.ttft if result.ttft is not None else result.total
            if best is None:
                best = result
                continue
            best_value = best.ttft if best.ttft is not None else best.total
            if current < best_value:
                best = result
    summary = {
        "best": _result_to_dict(best) if best else None,
        "workers": [_result_to_dict(res) for res in results],
    }
    return summary, best.response if best else None


async def analyze_file(path: str, cfg: AppConfig, rules: RulesClassifier,
                      media: MediaHelper, ai_workers: Optional[List[AIWorker]], *,
                      timeout: int = 120) -> tuple[dict, Optional[FileNode], Optional[ClassifiedPath], Optional[str]]:
    """Perform comprehensive file analysis using all available tools."""
    try:
        abs_path = os.path.abspath(path)
        name = os.path.basename(abs_path)
        # Get relative path from current directory
        rel_path = os.path.relpath(abs_path)
        print(f"Processing file: {abs_path}", file=sys.stderr)
        print(f"Relative path: {rel_path}", file=sys.stderr)
    except Exception as e:
        print(f"Error processing path {path}: {e}", file=sys.stderr)
        rel_path = path
        name = os.path.basename(path)
    
    mime = detect_mime(path)

    rule_match = rules.match(name, rel_path, mime)
    rule_info = {
        "pattern": None,
        "mime": None,
        "category": "Unknown",
        "action": "disaggregate",
        "mode": "ai",
        "line_number": None,
    }
    if rule_match:
        compiled = rule_match.rule
        rule_info.update({
            "pattern": compiled.path_pattern or "*",
            "mime": compiled.mime_pattern or "*",
            "category": str(compiled.category_path),
            "action": str(compiled.folder_action) if compiled.folder_action else "disaggregate",
            "mode": "ai" if compiled.requires_ai else "final",
            "line_number": compiled.line_number,  # type: ignore[dict-item]
        })
        capture_groups = rule_match.named_groups()
        if capture_groups:
            rule_info["groups"] = capture_groups  # type: ignore[assignment]
    rule_summary = rule_info["category"]
    pattern_summary = rule_info["pattern"] or "*"
    line_info = f"line={rule_info['line_number']}" if rule_info.get("line_number") else "line=?"
    print(f"Rules classifier => {rule_summary} (pattern={pattern_summary}, {line_info})", file=sys.stderr)

    file_metadata = collect_file_metadata(abs_path, mime)
    if rule_match:
        for key, value in rule_match.named_groups().items():
            file_metadata.add(key, value)
    collected_meta = file_metadata.to_dict()

    metadata = {
        "basic": {
            "name": name,
            "size": collected_meta.get("size"),
            "modified": collected_meta.get("modified"),
            "mime": mime
        },
        "media": {},
        "collected": collected_meta
    }
    if metadata["basic"]["size"] is None or metadata["basic"]["modified"] is None:
        try:
            stat_result = os.stat(path)
            metadata["basic"].setdefault("size", stat_result.st_size)
            metadata["basic"].setdefault("modified", stat_result.st_mtime)
        except OSError:
            pass

    sample_text_cache: Optional[str] = None
    try:
        # Future: add media-specific extraction here
        sample_text_cache = peek_text(path, mime, cfg.MAX_CONTENT_PEEK)
        if sample_text_cache:
            metadata["content_preview"] = sample_text_cache[:1000]  # type: ignore[assignment]
    except Exception as e:
        metadata["errors"] = [str(e)]  # type: ignore[assignment]
    
    ai_summary: Optional[Dict[str, Any]] = None
    best_response: Optional[ClassifierResponse] = None
    if ai_workers:
        try:
            sample_text = sample_text_cache or ""
            if sample_text_cache is None:
                print("Warning: No text sample available; using empty sample", file=sys.stderr)
            else:
                print(f"Got text sample ({len(sample_text)} bytes)", file=sys.stderr)
            clean_path = rel_path.replace('\\', '/').lstrip('/')
            hint: Dict[str, Any] = {
                "source_path": abs_path,
                "metadata": collected_meta,
            }
            if rule_match and rule_match.rule.requires_ai:
                compiled = rule_match.rule
                hint["rule_category_path"] = str(compiled.category_path)
                hint["rule"] = {
                    "path_pattern": compiled.path_pattern or "*",
                    "mime_pattern": compiled.mime_pattern or "*",
                    "folder_action": compiled.folder_action or "",
                    "requires_ai": compiled.requires_ai,
                }
            print(f"Racing AI classification across {len(ai_workers)} workers for {clean_path} ({mime})", file=sys.stderr)
            ai_summary, best_response = await _classify_all_workers(ai_workers, name, clean_path, mime, sample_text, hint, timeout)
            if ai_summary.get("best"):
                print(f"Fastest worker: {ai_summary['best']['worker']} -> {ai_summary['best'].get('category')}", file=sys.stderr)
            else:
                print("No successful AI classifications", file=sys.stderr)
        except Exception as exc:
            print(f"Error during AI classification: {exc}", file=sys.stderr)
            ai_summary = {"best": None, "workers": [], "error": str(exc)}
    
    destination_obj = None
    file_node: Optional[FileNode] = None
    preview_text = metadata.get("content_preview")
    if preview_text and not isinstance(preview_text, str):
        preview_text = None
    try:
        dest_category = None
        if best_response and best_response.path:
            dest_category = best_response.path if isinstance(best_response.path, CategoryPath) else CategoryPath(best_response.path)
        elif rule_match and rule_match.rule:
            dest_category = rule_match.rule.category_path
        else:
            dest_category = CategoryPath("Unknown")
        # Get file size for folder samples
        file_size = metadata["basic"].get("size", 0) or 0
        
        # For CLI, we need to call AI on each folder individually
        # Use shared logic to build folder action map
        from cli.cli_shared import build_folder_actions_for_path
        
        folder_classifier = ai_workers[0].classifier if ai_workers else None
        folder_actions, folder_decisions, folder_details = build_folder_actions_for_path(
            abs_path,
            mime,
            file_size,
            rules,
            folder_classifier,
            cfg.SOURCES,
            cfg.SOURCE_WRAPPER_REGEX,
        )
        
        # Debug: print folder decisions
        if folder_decisions:
            print("\nFolder AI Decisions:", file=sys.stderr)
            for folder_path, decision in folder_decisions.items():
                action = folder_actions.get(folder_path, "unknown")
                print(f"  {folder_path} -> {action} ({decision})", file=sys.stderr)

        node_builder = FileNodeBuilder(
            sources=cfg.SOURCES,
            folder_action_map=folder_actions,
            folder_decisions=folder_decisions,
            folder_details=folder_details,
            source_wrapper_pattern=cfg.SOURCE_WRAPPER_REGEX,
        )
        file_node = node_builder.build(
            abs_path,
            category=dest_category,
            rule_category=rule_match.rule.category_path if rule_match and rule_match.rule else None,
            ai_category=dest_category if best_response else None,
            mime=mime,
            metadata=file_metadata,
            rule_match=rule_match,
            classifier_origin=None,
            preview=preview_text,  # type: ignore[arg-type]
        )
        destination_obj = media.build_destination(file_node)
    except Exception as exc:
        destination_obj = None
        print(f"Failed to build destination preview: {exc}", file=sys.stderr)

    classified_payload = None
    if destination_obj:
        classified_payload = {
            "source": destination_obj.source,
            "destination": destination_obj.destination,
            "layers": [
                {"role": layer.role, "parts": list(layer.parts)}
                for layer in destination_obj.layers
            ],
            "explanation": destination_obj.explanation(),
        }

    # Extract folder decisions from file_node if available
    folder_decisions_dict = {}
    folder_details_list = []
    if file_node and file_node.folder_decisions:
        folder_decisions_dict = dict(file_node.folder_decisions)
    if file_node and file_node.folder_details:
        folder_details_list = list(file_node.folder_details)

    payload = {
        "path": path,
        "rule_analysis": rule_info,
        "metadata": metadata,
        "ai_classification": ai_summary,
        "ai_best_response": str(best_response.path) if best_response else None,
        "classified_path": classified_payload,
        "folder_decisions": folder_decisions_dict,
        "folder_details": folder_details_list,
    }
    return payload, file_node, destination_obj, preview_text  # type: ignore[return-value]

def write_output_json(result: dict, destination: str) -> None:
    target = Path(destination).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")


def validate_expectations(result: dict, expect_disaggregate: list[str], expect_keep: list[str]) -> int:
    """Validate folder action expectations.
    
    Returns:
        0 if all expectations met, 1 otherwise
    """
    if not expect_disaggregate and not expect_keep:
        return 0
    
    # ANSI color codes
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    NC = '\033[0m'
    
    folder_decisions = result.get("folder_decisions", {})
    if not folder_decisions:
        print(f"\n{RED}ERROR: No folder decisions found - cannot validate expectations{NC}", file=sys.stderr)
        return 1
    
    failed = False
    
    # Check disaggregate expectations
    for folder_name in expect_disaggregate:
        found = False
        for path, decision in folder_decisions.items():
            if path.rstrip('/').endswith('/' + folder_name) or path == '/' + folder_name:
                found = True
                if 'disaggregate' not in decision.lower():
                    print(f"{RED}✗ FAILED: Expected '{folder_name}' to be disaggregated, but got: {decision}{NC}", file=sys.stderr)
                    failed = True
                else:
                    print(f"{GREEN}✓ PASSED: '{folder_name}' is disaggregated ({decision}){NC}")
                break
        if not found:
            print(f"{RED}✗ FAILED: Folder '{folder_name}' not found in folder decisions{NC}", file=sys.stderr)
            failed = True
    
    # Check keep expectations
    for folder_name in expect_keep:
        found = False
        for path, decision in folder_decisions.items():
            if path.rstrip('/').endswith('/' + folder_name) or path == '/' + folder_name:
                found = True
                if 'move_as_unit' not in decision.lower() and 'keep' not in decision.lower():
                    print(f"{RED}✗ FAILED: Expected '{folder_name}' to be kept, but got: {decision}{NC}", file=sys.stderr)
                    failed = True
                else:
                    print(f"{GREEN}✓ PASSED: '{folder_name}' is kept ({decision}){NC}")
                break
        if not found:
            print(f"{RED}✗ FAILED: Folder '{folder_name}' not found in folder decisions{NC}", file=sys.stderr)
            failed = True
    
    return 1 if failed else 0


def format_output(result: dict, ai_available: bool = False) -> None:
    """Format and print analysis results."""

    print(f"\nAnalyzing: {result['path']}")
    print("\n=== Rule Analysis ===")
    for key, value in result["rule_analysis"].items():
        if key == "groups" and value:
            print("\nCapture Groups:")
            for group, val in value.items():
                print(f"  {group}: {val}")
        else:
            print(f"{key}: {value}")
    
    print("\n=== Destination Preview ===")
    classified = result.get("classified_path")
    if classified:
        print(f"  source: {classified['source']}")
        print(f"  destination: {classified['destination']}")
        print(f"  explanation: {classified['explanation']}")
        layers = classified.get("layers") or []
        if layers:
            print("  layers:")
            for layer in layers:
                print(f"    - {layer['role']}: {'/'.join(layer['parts'])}")
    else:
        print("  (no destination preview)")
    
    # Show folder decisions with details
    folder_details = result.get("folder_details")
    if folder_details:
        print("\n=== Folder Action Decisions (Detailed) ===")
        for detail in folder_details:
            print(f"\nFolder: {detail['folder_path']}")
            print(f"  Name: {detail['folder_name']}")
            print(f"  Total Files: {detail['total_files']}")
            print(f"  Children: {len(detail['children'])} items")
            if detail['children']:
                for child in detail['children'][:3]:  # Show first 3
                    child_type = child.get('type', 'unknown')
                    files_info = f" ({child.get('files_inside', 0)} files)" if child_type == 'dir' else ""
                    print(f"    - {child['name']} [{child_type}]{files_info}")
                if len(detail['children']) > 3:
                    print(f"    ... and {len(detail['children']) - 3} more")
            
            print(f"  Decision Chain:")
            for entry in detail['decision_chain']:
                classifier = entry['classifier']
                action = entry['action']
                hint = entry['hint']
                reason = entry['reason']
                is_final = "FINAL" if entry['is_final'] else "DELEGATE"
                
                if action:
                    print(f"    [{classifier}] {is_final} → {action} (reason: {reason})")
                elif hint:
                    print(f"    [{classifier}] {is_final} → hint:{hint} (reason: {reason})")
                else:
                    print(f"    [{classifier}] {is_final} (reason: {reason})")
            
            print(f"  Final Decision: {detail['final_action']} (source: {detail['final_source']})")
    else:
        # Fallback to simple view
        folder_decisions = result.get("folder_decisions")
        if folder_decisions:
            print("\n=== Folder Decisions (Simple) ===")
            for folder_path, decision in sorted(folder_decisions.items()):
                print(f"  {folder_path}: {decision}")

    print("\n=== Metadata ===")
    meta = result["metadata"]
    print("\nBasic Information:")
    for key, value in meta["basic"].items():
        print(f"  {key}: {value}")
    
    if meta.get("media"):
        print("\nMedia Metadata:")
        for key, value in meta["media"].items():
            print(f"\n  {key}:")
            if isinstance(value, dict):
                for k, v in value.items():
                    print(f"    {k}: {v}")
            else:
                print(f"    {value}")
    
    if meta.get("content_preview"):
        print("\nContent Preview:")
        print("-" * 80)
        print(meta["content_preview"])
        print("-" * 80)
    
    print("\n=== AI Classification ===")
    if not ai_available:
        print("AI classification skipped - Ollama service not configured or unavailable")
    else:
        ai_data = result.get("ai_classification")
        if not ai_data:
            print("No AI classification result available")
        else:
            if ai_data.get("error"):
                print(f"Overall error: {ai_data['error']}")
            best = ai_data.get("best")
            if best:
                ttft = best.get("ttft")
                total = best.get("total_duration")
                details = []
                if ttft is not None:
                    details.append(f"ttft={ttft:.3f}s")
                if total is not None:
                    details.append(f"total={total:.3f}s")
                print(f"Fastest worker: {best.get('worker')} -> {best.get('category') or 'Unknown'}", end="")
                if details:
                    print(f" ({', '.join(details)})")
                else:
                    print()
                if best.get("error"):
                    print(f"  Error: {best['error']}")
                if best.get("error_context"):
                    print("  Error Context:")
                    for key, value in best["error_context"].items():
                        print(f"    {key}: {value}")
            else:
                print("No successful AI classifications")
            workers = ai_data.get("workers") or []
            if workers:
                print("\nWorker timings:")
                for entry in workers:
                    stats = []
                    ttft = entry.get("ttft")
                    total = entry.get("total_duration")
                    if ttft is not None:
                        stats.append(f"ttft={ttft:.3f}s")
                    if total is not None:
                        stats.append(f"total={total:.3f}s")
                    if entry.get("timed_out"):
                        stats.append("timed_out")
                    status = ", ".join(stats) if stats else "no timing data"
                    success = entry.get("success")
                    category = entry.get("category") or "Unknown"
                    marker = "✓" if success else "✗"
                    print(f"  {marker} {entry.get('worker')}: {category} ({status})")
                    if entry.get("error"):
                        print(f"    Error: {entry['error']}")
                    if entry.get("metrics"):
                        metrics = entry["metrics"]
                        interesting = ["attempt", "endpoint", "throttle_wait", "timeout_s", "prompt_size", "response_size"]
                        print("    Metrics:")
                        for key, value in metrics.items():
                            if key in interesting or isinstance(value, (float, int, str)):
                                if isinstance(value, float):
                                    print(f"      {key}: {value:.3f}")
                                else:
                                    print(f"      {key}: {value}")

    
    print("\n" + "=" * 80 + "\n")


def save_result_to_db(cfg: AppConfig, file_node: FileNode, destination: ClassifiedPath) -> None:
    db = Database(cfg)
    record_builder = ClassificationRecordBuilder(cfg)
    record = record_builder.build(file_node, destination)
    db.update_category_dest([record])
    print(f"Persisted classification for {file_node.physical_path} -> {destination.destination}")


def dump_db(cfg: AppConfig, *, path_filter: str | None, limit: int, json_output: bool) -> None:
    db = Database(cfg)
    with db.connect() as con:
        cur = con.cursor()
        query = (
            "SELECT path, dest, category, rule_category, ai_category, metadata_json, preview, file_json "
            "FROM files WHERE metadata_json IS NOT NULL"
        )
        params: list = []
        if path_filter:
            query += " AND path LIKE ?"
            params.append(f"%{path_filter}%")
        query += " ORDER BY path"
        if limit and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        rows = cur.execute(query, params).fetchall()

    records = [
        ClassificationRecord.from_db_row(
            (path, dest, category, rule_category, ai_category, metadata_json, preview, file_json)
        )
        for path, dest, category, rule_category, ai_category, metadata_json, preview, file_json in rows
    ]

    if json_output:
        print(json.dumps([record.export() for record in records], indent=2, ensure_ascii=False))
        return

    if not records:
        print("No stored records matched the query.")
        return

    for record in records:
        print("=" * 80)
        print(f"Path: {record.path}")
        print(f"Destination: {record.destination or '<pending>'}")
        print(f"Category: {record.category_label}")
        if record.rule_category_label or record.ai_category_label:
            print(
                "Rule/AI:",
                record.rule_category_label or "<none>",
                "/",
                record.ai_category_label or "<none>",
            )
        if record.preview:
            print("Preview:")
            print(record.preview)
        metadata = record.parsed_metadata()
        if isinstance(metadata, dict):
            print("Metadata:")
            for key, value in metadata.items():
                print(f"  {key}: {value}")
        elif metadata is not None:
            print("Metadata:")
            print(json.dumps(metadata, indent=2, ensure_ascii=False))
        elif record.metadata_json:
            print("Metadata (raw):")
            print(record.metadata_json)
    print("=" * 80)

async def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive file and DB analyzer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("path", nargs="?", help="File path (file mode) or substring filter (db mode)")
    parser.add_argument("--mode", choices=["file", "db"], default="file", help="Select analyzer mode")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI classification (file mode)")
    parser.add_argument("--ollama-url", help="Override Ollama URL from environment (file mode)")
    parser.add_argument("--ollama-prompt", help="Path to a custom Ollama system prompt template (file mode)")
    parser.add_argument("--output-json", nargs='?', const=True, help="Output JSON (db mode: to stdout; file mode: to specified file)")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout for AI requests in seconds (file mode)")
    parser.add_argument("--save", action="store_true", help="Persist classification result back into the database (file mode)")
    parser.add_argument("--db-limit", type=int, default=20, help="Row limit when dumping the database (db mode)")
    parser.add_argument("--path-filter", help="Substring filter when dumping the database (db mode)")
    parser.add_argument("--expect-disaggregate", action="append", dest="expect_disaggregate", metavar="NAME",
                        help="Assert that folder NAME is disaggregated (can be used multiple times)")
    parser.add_argument("--expect-keep", action="append", dest="expect_keep", metavar="NAME",
                        help="Assert that folder NAME is kept as unit (can be used multiple times)")
    args = parser.parse_args()

    cfg = AppConfig.from_env()

    if args.mode == "db":
        dump_db(cfg, path_filter=args.path or args.path_filter, limit=args.db_limit, json_output=bool(args.output_json))
        return

    if not args.path:
        parser.error("path is required in file mode")

    rules = RulesClassifier()
    media = MediaHelper(cfg)

    prompt_template: Optional[str] = None
    if args.ollama_prompt:
        try:
            prompt_template = _read_prompt_template(args.ollama_prompt)
        except Exception as exc:
            parser.error(str(exc))

    ai_workers: List[AIWorker] = []
    base_classifiers: List[OllamaClassifier] = []
    if not args.no_ai:
        try:
            ai_workers, base_classifiers = await _init_ai_workers(cfg, args.ollama_url, prompt_template)
            if not ai_workers:
                print("Error: No available AI workers and AI is required. Check OLLAMA_URL.", file=sys.stderr)
                sys.exit(1)
        except Exception as exc:
            print(f"Error: Failed to initialize AI workers: {exc}", file=sys.stderr)
            sys.exit(1)

    path = os.path.abspath(args.path)
    if not os.path.exists(path):
        print(f"ERROR: File not found: {path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(path):
        print(f"ERROR: Not a file: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        result, file_node, destination_obj, _ = await analyze_file(
            path,
            cfg,
            rules,
            media,
            ai_workers if ai_workers else None,
            timeout=args.timeout,
        )
        if args.output_json and args.output_json is not True:
            write_output_json(result, args.output_json)
        format_output(result, ai_available=bool(ai_workers))

        if args.save:
            if not file_node or not destination_obj:
                print("Nothing to save (destination unavailable).", file=sys.stderr)
            else:
                save_result_to_db(cfg, file_node, destination_obj)
        
        # Validate expectations if provided
        exit_code = validate_expectations(result, args.expect_disaggregate or [], args.expect_keep or [])
        return exit_code
    finally:
        for classifier in base_classifiers:
            try:
                await classifier.close()
            except Exception:
                pass

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    if exit_code:
        sys.exit(exit_code)
