#!/usr/bin/env python3
"""Test folder actions for a specific directory tree."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.classifiers import RulesClassifier, OllamaClassifier, OpenAIClassifier
from app.folder_policy import collect_folder_samples, build_folder_action_map
from app.folder_action import FolderAction


def create_classifier(cfg: AppConfig):
    """Create appropriate classifier based on config."""
    if cfg.CLASSIFIER_KIND == "manual":
        return None
    
    endpoints = cfg.ollama_endpoints()
    if not endpoints:
        return None
    
    url, workers, model = endpoints[0]
    
    # Detect API type
    if "openai" in url.lower() or ":1234" in url:
        from app.classifiers.ai_auto import load_prompt
        prompt = load_prompt("prompts/folder_action.txt")
        return OpenAIClassifier(url=url, model=model, folder_prompt_template=prompt, max_concurrency=workers)
    else:
        from app.classifiers.ai_auto import load_prompt
        prompt = load_prompt("prompts/folder_action.txt")
        return OllamaClassifier(url=url, model=model, folder_prompt_template=prompt, max_concurrency=workers)


def main():
    if len(sys.argv) < 2:
        print("Usage: test_folder_actions.py <directory_path>")
        sys.exit(1)
    
    target_dir = sys.argv[1]
    if not os.path.exists(target_dir):
        print(f"Error: Path does not exist: {target_dir}")
        sys.exit(1)
    
    print(f"Analyzing folder actions for: {target_dir}\n")
    
    # Collect all files
    file_list = []
    for root, dirs, files in os.walk(target_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                size = os.path.getsize(fpath)
                file_list.append((fpath, "*", size))
            except:
                pass
    
    print(f"Found {len(file_list)} files")
    
    # Collect folder samples
    samples = collect_folder_samples(file_list)
    print(f"Generated samples for {len(samples)} folders\n")
    
    # Build folder action map
    cfg = AppConfig.from_env()
    rules = RulesClassifier()
    if not rules.ensure_available():
        print("ERROR: Rules classifier not available")
        sys.exit(1)
    
    classifier = create_classifier(cfg)
    if classifier:
        if not classifier.ensure_available():
            print("WARNING: AI classifier not available, using rules only")
            classifier = None
        else:
            print(f"Using AI classifier: {classifier.display_name()}\n")
    
    folder_actions, folder_decisions = build_folder_action_map(rules, classifier, samples, cfg.SOURCES, cfg.SOURCE_WRAPPER_REGEX)
    
    # Sort by path depth for display (parent → child)
    sorted_actions = sorted(folder_actions.items(), key=lambda x: x[0].count('/'))
    
    print("\n" + "=" * 100)
    print("FOLDER ACTIONS (parent → child order)")
    print("=" * 100)
    
    # Group by action type for summary
    action_counts = {action: 0 for action in FolderAction}
    decision_sources = {}
    inherited_count = 0
    
    for folder_path, action in sorted_actions:
        depth = folder_path.count('/')
        indent = "  " * max(0, depth - target_dir.count('/'))
        folder_name = os.path.basename(folder_path) or folder_path
        decision = folder_decisions.get(folder_path, "unknown")
        
        # Extract decision source
        source = decision.split(":")[0] if ":" in decision else decision
        decision_sources[source] = decision_sources.get(source, 0) + 1
        action_counts[action] += 1
        
        if source == "inherited":
            inherited_count += 1
        
        # Color-coded output
        action_symbol = {
            FolderAction.KEEP: "🔒 KEEP",
            FolderAction.DISAGGREGATE: "📦 SPLIT",
            FolderAction.STRIP: "🗑️  STRIP",
        }.get(action, str(action))
        
        # Mark inherited decisions
        if source == "inherited":
            print(f"{indent}{folder_name:60s} → {action_symbol:12s} [inherited from parent]")
        else:
            print(f"{indent}{folder_name:60s} → {action_symbol:12s} [{decision}]")
    
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"Total folders discovered:        {len(samples):,}")
    print(f"Total folders with actions:      {len(folder_actions):,}")
    print(f"  - Inherited from kept parent:  {inherited_count:,}")
    print(f"  - Directly classified:         {len(folder_actions) - inherited_count:,}")
    print()
    print("Actions breakdown:")
    for action, count in action_counts.items():
        if count > 0:
            print(f"  {action.value:15s} {count:5,}")
    print()
    print("Decision sources:")
    for source, count in sorted(decision_sources.items()):
        print(f"  {source:15s} {count:5,}")


if __name__ == "__main__":
    main()
