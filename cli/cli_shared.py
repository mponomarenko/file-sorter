"""Shared logic for CLI tools - folder walking, action resolution."""
from pathlib import PurePosixPath
from typing import Optional

from app.classifiers import RulesClassifier, Classifier
from app.classifiers.base import FolderActionResponse
from app.folder_policy import FolderSample, normalize_action_map, _strip_sources
from app.folder_action import FolderAction, RequiresAI, FolderActionRequest


def build_folder_actions_for_path(
    path: str,
    mime: str,
    file_size: int,
    rules: RulesClassifier,
    ai_classifier: Optional[Classifier],
    sources: list[str],
    source_wrapper_pattern: str | None = None,
) -> tuple[dict[str, FolderAction], dict[str, str], list[dict]]:
    """
    Build folder action map by walking up from file to root.
    
    For CLI single-file analysis, we need to call AI on each folder individually
    (unlike batch processing which uses recursion to handle parent folders naturally).
    
    Args:
        path: Absolute path to file
        mime: MIME type of file
        file_size: Size of file in bytes
        rules: Rules classifier for checking folder rules
        ai_classifier: Optional AI classifier for folder decisions
        sources: List of source root paths
        
    Returns:
        Tuple of (actions_map, decisions_map) where:
        - actions_map: Dict mapping folder path to action (disaggregate/keep/keep_parent)
        - decisions_map: Dict mapping folder path to decision source
    """
    folder_actions: dict[str, FolderAction] = {}
    folder_decisions: dict[str, str] = {}
    folder_details: list[dict] = []
    
    # Walk down the path and call AI/rules on each folder (top-down: root to deepest)
    p = PurePosixPath(path)
    path_parts = list(p.parts)  # All path components including filename
    
    # Process from root down to deepest folder (top-down order)
    for i in range(0, len(path_parts) - 1):  # -1 to skip filename
        folder_parts = path_parts[:i+1]
        # Skip empty parts and root slash, then build proper path
        folder_parts = [part for part in folder_parts if part and part != "/"]
        if not folder_parts:
            continue
        folder_path = "/" + "/".join(folder_parts)
        
        # If parent was kept, inherit that decision (skip AI call)
        if i > 0:
            parent_parts = path_parts[:i]
            parent_parts = [part for part in parent_parts if part and part != "/"]
            if parent_parts:
                parent_path = "/" + "/".join(parent_parts)
                parent_action = folder_actions.get(parent_path)
                if parent_action == FolderAction.KEEP:
                    folder_actions[folder_path] = FolderAction.KEEP
                    folder_decisions[folder_path] = f"{FolderAction.KEEP.value}:inherited:parent_kept:{parent_path}"
                    folder_details.append({
                        "folder_path": folder_path,
                        "folder_name": folder_parts[-1],
                        "children": [],
                        "total_files": 1,
                        "decision_chain": [{
                            "classifier": "inheritance",
                            "is_final": True,
                            "action": FolderAction.KEEP.value,
                            "hint": None,
                            "reason": f"parent_kept:{parent_path}",
                        }],
                        "final_action": FolderAction.KEEP.value,
                        "final_source": f"inherited:parent_kept:{parent_path}",
                    })
                    continue
        
        # Build sample with direct child
        sample = _build_folder_sample(path_parts, i, mime, file_size)
        
        # Use classifier chain (Rules → AI → Default)
        rel_folder = _strip_sources(folder_path, sources, source_wrapper_pattern)
        payload = sample.payload(rel_folder)
        request = FolderActionRequest(
            folder_path=payload["folder_path"],
            folder_name=payload["folder_name"],
            children=payload["children"],
            total_files=payload["total_files"],
            rule_hint=None,
        )
        
        # Walk classifier chain
        classifiers_chain: list[tuple[Classifier, str]] = [(rules, "rules")]
        if ai_classifier:
            classifiers_chain.append((ai_classifier, ai_classifier.display_name()))
        
        action = None
        decision_source = None
        decision_chain = []
        
        for classifier, classifier_name in classifiers_chain:
            response = classifier.advise_folder_action(request)
            
            chain_entry = {
                "classifier": classifier_name,
                "is_final": response.is_final,
                "action": response.action.value if response.action else None,
                "hint": response.hint.value if response.hint else None,
                "reason": response.reason,
            }
            decision_chain.append(chain_entry)
            
            if response.is_final:
                if response.action is None:
                    # Should not happen but handle gracefully
                    request.rule_hint = response.hint
                    continue
                action = response.action
                decision_source = f"{action.value}:{classifier_name}:{response.reason}"
                break
            
            # Delegate - update hint and continue
            request.rule_hint = response.hint
        
        # Chain exhausted
        if action is None:
            action = request.rule_hint or FolderAction.DISAGGREGATE
            decision_source = f"{action.value}:default:chain_exhausted"
            decision_chain.append({
                "classifier": "default",
                "is_final": True,
                "action": action.value,
                "hint": None,
                "reason": "chain_exhausted",
            })
        
        folder_actions[folder_path] = action
        folder_decisions[folder_path] = decision_source or f"{action.value}:unknown"
        folder_details.append({
            "folder_path": folder_path,
            "folder_name": payload["folder_name"],
            "children": payload["children"],
            "total_files": payload["total_files"],
            "decision_chain": decision_chain,
            "final_action": action.value,
            "final_source": decision_source,
        })
    
    return normalize_action_map(folder_actions), folder_decisions, folder_details


def _build_folder_sample(
    path_parts: list[str],
    folder_index: int,
    mime: str,
    file_size: int,
) -> FolderSample:
    """
    Build a FolderSample showing only the direct child.
    
    Args:
        path_parts: All path components from root to file
        folder_index: Index of current folder in path_parts
        mime: MIME type of the file (if direct child is file)
        file_size: Size of the file (if direct child is file)
        
    Returns:
        FolderSample with one direct child (either subdir or file)
    """
    sample = FolderSample()
    
    if folder_index + 1 < len(path_parts) - 1:
        # Has subdir child (not the file itself)
        child_name = path_parts[folder_index + 1]
        sample.add_child(child_name, is_dir=True, mime="*", size=0)
    else:
        # Direct file child (last part is the filename)
        sample.add_child(path_parts[-1], is_dir=False, mime=mime, size=file_size)
    
    sample.total_files = 1
    return sample
