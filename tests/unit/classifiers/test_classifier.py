import sys
from pathlib import Path
import asyncio

# Ensure we can import `app.*`
TOP = Path(__file__).resolve().parents[3]
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.categories import CategoryPath
from app.classifiers import RulesClassifier, ClassifierResponse


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        # Ensure we clean up any remaining event loop
        if asyncio._get_running_loop() is not None:
            asyncio._set_running_loop(None)


def test_rules_classifier_classifies_source_code():
    clf = RulesClassifier()
    response = run(clf.classify("main.py", "proj/main.py", "text/x-python", "print('hi')"))
    assert isinstance(response, ClassifierResponse)
    assert isinstance(response.path, CategoryPath)
    assert str(response.path) == "Software/Source_Code"


def test_rules_classifier_classifies_system_paths():
    clf = RulesClassifier()
    response = run(clf.classify("x.cpython-312.pyc", "pkg/__pycache__/x.cpython-312.pyc", "application/octet-stream", ""))
    assert isinstance(response, ClassifierResponse)
    assert isinstance(response.path, CategoryPath)
    assert str(response.path) == "System"

def test_rules_classifier_folder_action_keeps_system_folders():
    """RulesClassifier detects keep_parent markers and returns decision."""
    from app.folder_action import FolderAction, FolderActionRequest
    from app.classifiers.base import FolderActionResponse
    clf = RulesClassifier()
    result = clf.advise_folder_action(FolderActionRequest(
        folder_path="/project",
        folder_name="project",
        children=[
            {"name": ".git", "type": "dir", "files_inside": 10},
            {"name": "src", "type": "dir", "files_inside": 5},
        ],
        total_files=15,
        rule_hint=None,
    ))
    # Should detect .git as keep_parent marker - final decision
    assert isinstance(result, FolderActionResponse)
    assert result.is_final
    assert result.action == FolderAction.KEEP


def test_rules_classifier_folder_action_no_markers():
    """RulesClassifier delegates when no rules match (needs AI)."""
    from app.folder_action import FolderAction, FolderActionRequest
    from app.classifiers.base import FolderActionResponse
    clf = RulesClassifier()
    result = clf.advise_folder_action(FolderActionRequest(
        folder_path="/misc",
        folder_name="misc",
        children=[
            {"name": "file.txt", "type": "file", "mime": "text/plain", "size": 100},
        ],
        total_files=1,
        rule_hint=None,
    ))
    # No markers, no explicit rules - delegates with hint
    assert isinstance(result, FolderActionResponse)
    assert not result.is_final  # Delegated
    assert result.hint == FolderAction.DISAGGREGATE  # Hint for next classifier
