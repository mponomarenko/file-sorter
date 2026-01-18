#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.categories import CategoryPath
from app.classifiers import RulesClassifier
from app.media import MediaHelper, detect_mime
from app.file_metadata import FileMetadata
from app.folder_policy import collect_folder_samples, build_folder_action_map
from app.file_nodes import FileNodeBuilder
from app.metadata import collect_file_metadata


def _describe_classified(path_obj) -> dict:
    if not path_obj:
        return {}
    return {
        "source": path_obj.source,
        "destination": path_obj.destination,
        "layers": [{"role": layer.role, "parts": list(layer.parts)} for layer in path_obj.layers],
        "explanation": path_obj.explanation(),
    }


def analyze_path(path: str, cfg: AppConfig, rules: RulesClassifier, media: MediaHelper) -> dict:
    abs_path = os.path.abspath(path)
    name = os.path.basename(abs_path.rstrip("/"))
    rel_path = os.path.relpath(abs_path)
    mime = detect_mime(abs_path) if os.path.isfile(abs_path) else "inode/directory"
    match = rules.match(name, rel_path, mime)
    compiled = match.rule if match else None
    info = {
        "path": abs_path,
        "mime": mime,
        "rule": {
            "line": compiled.line_number if compiled else None,
            "path_pattern": compiled.path_pattern or "*" if compiled else "*",
            "mime_pattern": compiled.mime_pattern or "*" if compiled else "*",
            "category": str(compiled.category_path) if compiled else "Unknown",
            "action": compiled.folder_action if compiled else "disaggregate",
            "requires_ai": bool(compiled.requires_ai) if compiled else False,
        },
        "groups": match.named_groups() if match else {},
    }
    # Get file size if it's a file
    file_size = os.path.getsize(abs_path) if os.path.isfile(abs_path) else 0
    samples = collect_folder_samples([(abs_path, mime, file_size)])
    folder_actions, folder_decisions = build_folder_action_map(
        rules,
        rules,
        samples,
        cfg.SOURCES,
        cfg.SOURCE_WRAPPER_REGEX,
    ) if samples else ({}, {})

    category = compiled.category_path if compiled else CategoryPath("Unknown")
    metadata = collect_file_metadata(abs_path, mime)
    if match:
        for key, value in match.named_groups().items():
            metadata.add(key, value)
    node_builder = FileNodeBuilder(
        sources=cfg.SOURCES,
        folder_action_map=folder_actions,
        source_wrapper_pattern=cfg.SOURCE_WRAPPER_REGEX,
    )
    file_node = node_builder.build(
        abs_path,
        category=category,
        rule_category=compiled.category_path if compiled else None,
        mime=mime,
        metadata=metadata,
        rule_match=match,
    )
    classified = media.build_destination(file_node)
    info["classified_path"] = _describe_classified(classified)
    return info


def analyze_directory(directory: str, cfg: AppConfig, rules: RulesClassifier, media: MediaHelper) -> list[dict]:
    results = []
    for root, _, files in os.walk(directory):
        for file_name in files:
            file_path = os.path.join(root, file_name)
            results.append(analyze_path(file_path, cfg, rules, media))
    return results


def write_results_json(results: list[dict], destination: str) -> None:
    target = Path(destination).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test rule matching for files and directories.")
    parser.add_argument("path", help="File or directory to analyze")
    parser.add_argument("--output-json", help="Write structured JSON analysis to a file")
    args = parser.parse_args()

    cfg = AppConfig.from_env()
    rules = RulesClassifier()
    media = MediaHelper(cfg)

    target = os.path.abspath(args.path)
    if os.path.isfile(target):
        results = [analyze_path(target, cfg, rules, media)]
    else:
        results = analyze_directory(target, cfg, rules, media)

    if args.output_json:
        write_results_json(results, args.output_json)

    for entry in results:
        print(f"\nAnalyzing: {entry['path']}")
        print(f"MIME: {entry['mime']}")
        print("Rule:")
        for key, value in entry["rule"].items():
            print(f"  {key}: {value}")
        if entry["groups"]:
            print("Capture groups:")
            for key, value in entry["groups"].items():
                print(f"  {key}: {value}")
        classified = entry.get("classified_path")
        if classified:
            print("Classified path:")
            print(f"  source: {classified['source']}")
            print(f"  destination: {classified['destination']}")
            print(f"  explanation: {classified['explanation']}")
            for layer in classified.get("layers", []):
                print(f"    - {layer['role']}: {'/'.join(layer['parts'])}")
        print("-" * 60)


if __name__ == "__main__":
    main()
