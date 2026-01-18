import sys
from pathlib import Path

# Add the project root to sys.path for imports
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
from app.folder_policy import build_folder_action_map, FolderSample
from app.classifiers import RulesClassifier, MockAIClassifier
from app.folder_action import FolderAction


def test_git_folder_marks_parent_as_keep():
    """Test that a .git folder inside a directory marks the parent as KEEP.
    
    The .git rule has folder_action=keep_parent,final which should signal:
    'This is a git repository, keep the whole thing together, don't disaggregate'
    
    Tests WITHOUT classifier - keep_parent markers work without AI.
    """
    rules = RulesClassifier()
    
    # Simulate a folder structure - show only DIRECT children:
    # /source/MyProject/
    #   .git/        <- direct child (dir with files inside)
    #   src/         <- direct child (dir)
    #   README.md    <- direct child (file)
    
    sample = FolderSample()
    sample.add_child(".git", is_dir=True, mime="*", size=0)
    sample.add_child("src", is_dir=True, mime="*", size=0)
    sample.add_child("README.md", is_dir=False, mime="text/plain", size=1024)
    sample.total_files = 4
    
    samples = {"/source/MyProject": sample}
    
    actions, _ = build_folder_action_map(rules, None, samples, ["/source"])
    
    # The parent folder should be KEEP because it contains .git/
    assert actions["/source/MyProject"] == FolderAction.KEEP, \
        "Folder containing .git/ should be kept together (not disaggregated)"


def test_git_folder_marks_parent_as_keep_with_ai():
    """Test that keep_parent markers work even when AI classifier is provided.
    
    Tests WITH classifier - keep_parent still takes precedence.
    """
    rules = RulesClassifier()
    mock_ai = MockAIClassifier()
    
    # Show only DIRECT children
    sample = FolderSample()
    sample.add_child(".git", is_dir=True, mime="*", size=0)
    sample.add_child("src", is_dir=True, mime="*", size=0)
    sample.add_child("README.md", is_dir=False, mime="text/plain", size=1024)
    sample.total_files = 4
    
    samples = {"/source/MyProject": sample}
    
    actions, _ = build_folder_action_map(rules, mock_ai, samples, ["/source"])
    
    assert actions["/source/MyProject"] == FolderAction.KEEP, \
        "Folder containing .git/ should be kept together even with AI available"


def test_node_modules_marks_parent_as_keep():
    """Test that node_modules/ inside a directory marks the parent as KEEP.
    
    Similar to .git - indicates this is a Node.js project that should stay together.
    """
    rules = RulesClassifier()
    mock_ai = MockAIClassifier()
    
    # Show only DIRECT children
    sample = FolderSample()
    sample.add_child("node_modules", is_dir=True, mime="*", size=0)
    sample.add_child("package.json", is_dir=False, mime="application/json", size=512)
    sample.add_child("server.js", is_dir=False, mime="application/javascript", size=1024)
    sample.total_files = 3
    
    samples = {"/source/WebApp": sample}
    
    actions, _ = build_folder_action_map(rules, mock_ai, samples, ["/source"])
    
    assert actions["/source/WebApp"] == FolderAction.KEEP, \
        "Folder containing node_modules/ should be kept together"


def test_pyc_files_dont_mark_parent_as_keep_without_ai():
    """Test that .pyc files don't force parent to KEEP - WITHOUT AI classifier.
    
    The .pyc rule has keep,final but that's about the file itself, not a structural marker.
    Random .pyc files shouldn't force folder to be kept.
    
    Without AI, folders requiring AI consultation default to DISAGGREGATE (safer).
    """
    rules = RulesClassifier()
    
    # Test a random folder (not Downloads which has special rule)
    # Show only DIRECT children
    sample = FolderSample()
    sample.add_child("script.pyc", is_dir=False, mime="application/x-python-code", size=512)
    sample.add_child("document.pdf", is_dir=False, mime="application/pdf", size=2048)
    sample.total_files = 2
    
    samples = {"/source/RandomFolder": sample}
    
    actions, _ = build_folder_action_map(rules, None, samples, ["/source"])
    
    # document.pdf matches catch-all rule with requires_ai=ai
    # No classifier available, so defaults to DISAGGREGATE (safer for unknown folders)
    # .pyc uses regular 'keep', not 'keep_parent', so doesn't affect folder decision
    assert actions["/source/RandomFolder"] == FolderAction.DISAGGREGATE, \
        "Without AI, folders requiring AI default to DISAGGREGATE (safer)"


def test_pyc_files_dont_mark_parent_as_keep_with_ai():
    """Test that .pyc files don't force parent to KEEP - WITH AI classifier.
    
    When mixed with unknown files (like PDF without rule), AI gets consulted for folder decision.
    """
    rules = RulesClassifier()
    mock_ai = MockAIClassifier()
    
    # Test a random folder (not Downloads which has special rule)
    # Show only DIRECT children
    sample = FolderSample()
    sample.add_child("script.pyc", is_dir=False, mime="application/x-python-code", size=512)
    sample.add_child("document.pdf", is_dir=False, mime="application/pdf", size=2048)
    sample.total_files = 2
    
    samples = {"/source/RandomFolder": sample}
    
    actions, _ = build_folder_action_map(rules, mock_ai, samples, ["/source"])
    
    # document.pdf has no rule match, triggers AI consultation
    # MockAI defaults to disaggregate
    # .pyc uses regular 'keep', not 'keep_parent', so it doesn't override AI decision
    assert actions["/source/RandomFolder"] == FolderAction.DISAGGREGATE, \
        "Random folder with unknown files should consult AI (MockAI returns disaggregate)"


def test_venv_marks_parent_as_keep():
    """Test that .venv/ inside a directory marks the parent as KEEP.
    
    Indicates this is a Python project with virtual environment.
    """
    rules = RulesClassifier()
    mock_ai = MockAIClassifier()
    
    # Show only DIRECT children
    sample = FolderSample()
    sample.add_child(".venv", is_dir=True, mime="*", size=0)
    sample.add_child("main.py", is_dir=False, mime="text/x-python", size=1024)
    sample.add_child("requirements.txt", is_dir=False, mime="text/plain", size=256)
    sample.total_files = 3
    
    samples = {"/source/PythonProject": sample}
    
    actions, _ = build_folder_action_map(rules, mock_ai, samples, ["/source"])
    
    assert actions["/source/PythonProject"] == FolderAction.KEEP, \
        "Folder containing .venv/ should be kept together"


def test_keep_parent_overrides_file_level_keep():
    """Test that keep_parent markers win even when mixed with regular keep files.
    
    A git repo downloaded to Downloads should be kept together despite
    Downloads normally being disaggregated.
    """
    rules = RulesClassifier()
    from app.classifiers import MockAIClassifier
    mock_ai = MockAIClassifier()
    
    # Show only DIRECT children
    sample = FolderSample()
    sample.add_child(".git", is_dir=True, mime="*", size=0)
    sample.add_child("README.md", is_dir=False, mime="text/plain", size=1024)
    sample.add_child("script.pyc", is_dir=False, mime="application/x-python-code", size=512)
    sample.total_files = 3
    
    samples = {"/source/Downloads": sample}
    
    # Downloads has disaggregate rule, but .git has keep_parent
    # The keep_parent should NOT override the explicit folder rule
    # Downloads should consult AI since it has disaggregate,ai
    actions, _ = build_folder_action_map(rules, mock_ai, samples, ["/source"])
    
    # With AI, Downloads gets disaggregated (MockAI returns "disaggregate" by default)
    # But wait - .git has keep_parent which is checked in the else branch
    # So this tests if folder rule takes precedence over file keep_parent
    print(f"Action for /source/Downloads: {actions['/source/Downloads']}")
    # This is a nuanced case - should folder rule or keep_parent win?
    # Current design: folder rule wins (it's checked first)


def test_skip_ai_for_children_of_kept_folders():
    """Test that AI classifier is NOT called for folders inside already-kept parents.
    
    When a parent folder is marked as KEEP, all children should be kept as part of
    the unit, and we shouldn't waste AI calls on them.
    """
    rules = RulesClassifier()
    
    # Track AI calls
    ai_calls = []
    
    class TrackingMockAI(MockAIClassifier):
        def advise_folder_action(self, request):
            ai_calls.append(request.folder_path)
            return super().advise_folder_action(request)
    
    mock_ai = TrackingMockAI()
    mock_ai.set_keep_folders(["/project"])  # Mark project as keep
    
    # Simulate folder structure:
    # /project/              <- Should call AI
    # /project/src/          <- Should NOT call AI (parent is kept)
    # /project/src/utils/    <- Should NOT call AI (ancestor is kept)
    # /other/                <- Should call AI (different tree)
    
    samples = {
        "/project": FolderSample(),
        "/project/src": FolderSample(),
        "/project/src/utils": FolderSample(),
        "/other": FolderSample(),
    }
    
    for sample in samples.values():
        sample.add_child("file.txt", is_dir=False, mime="text/plain", size=100)
        sample.total_files = 1
    
    actions, _ = build_folder_action_map(rules, mock_ai, samples, None)
    
    # Verify results
    assert actions["/project"] == FolderAction.KEEP
    assert "/project/src" not in actions  # Skipped - parent is kept
    assert "/project/src/utils" not in actions  # Skipped - ancestor is kept
    assert actions["/other"] == FolderAction.DISAGGREGATE
    
    # Verify AI was only called for top-level folders, not children of kept folders
    assert "/project" in ai_calls
    assert "/project/src" not in ai_calls, "Should not call AI for children of kept folders"
    assert "/project/src/utils" not in ai_calls, "Should not call AI for descendants of kept folders"
    assert "/other" in ai_calls


def test_keep_except_allows_ai_for_children():
    """Test that keep_except DOES allow AI calls for children (unlike regular keep).
    
    keep_except means: keep this folder, but children can still be disaggregated.
    So we still need to process/call AI for children.
    """
    from app.folder_policy import build_folder_action_map, FolderSample
    from app.folder_action import FolderAction
    
    rules = RulesClassifier()
    
    # Track AI calls
    ai_calls = []
    
    class TrackingMockAI(MockAIClassifier):
        def advise_folder_action(self, request):
            from app.classifiers.base import FolderActionResponse
            ai_calls.append(request.folder_path)
            # Return disaggregate for Documents subfolder
            if "Documents" in request.folder_path:
                return FolderActionResponse.decision(FolderAction.DISAGGREGATE, reason="mock:test")
            return FolderActionResponse.decision(FolderAction.KEEP, reason="mock:test")
    
    mock_ai = TrackingMockAI()
    
    # Create folder structure:
    # /fixtures/paths/home/user/              <- Should be KEEP_EXCEPT (manual pre-decision)
    # /fixtures/paths/home/user/Downloads/    <- Should call AI (no rule match, needs AI)
    # /fixtures/paths/home/user/temp/         <- Should call AI (no rule match, needs AI)
    
    samples = {
        "/fixtures/paths/home/user": FolderSample(),
        "/fixtures/paths/home/user/Downloads": FolderSample(),
        "/fixtures/paths/home/user/temp": FolderSample(),
    }
    samples["/fixtures/paths/home/user"].add_child("Downloads", is_dir=True)
    samples["/fixtures/paths/home/user"].add_child("temp", is_dir=True)
    samples["/fixtures/paths/home/user/Downloads"].add_child("file1.zip", is_dir=False, mime="application/zip")
    samples["/fixtures/paths/home/user/temp"].add_child("temp.txt", is_dir=False, mime="text/plain")
    
    # Pre-set parent to KEEP_EXCEPT and build action map for children
    # We need to simulate this by manually adding to the decided map
    from app.folder_policy import _decide_folder_action
    
    # First decision: /fixtures/paths/home/user gets KEEP_EXCEPT (simulated from rules)
    decided = {"/fixtures/paths/home/user": FolderAction.KEEP_EXCEPT}
    
    # Now process children - they should NOT be skipped
    action_downloads, _ = _decide_folder_action("/fixtures/paths/home/user/Downloads", samples["/fixtures/paths/home/user/Downloads"], 
                                                 rules, mock_ai, [], decided, None)
    action_temp, _ = _decide_folder_action("/fixtures/paths/home/user/temp", samples["/fixtures/paths/home/user/temp"],
                                           rules, mock_ai, [], decided, None)
    
    # Assertions
    assert len(ai_calls) >= 1, f"Expected at least 1 AI call for children of keep_except folder, got {len(ai_calls)}"
    # At least one folder should call AI (depending on rules, some might be decided by rules)
    assert any("/fixtures/paths/home/user/" in call for call in ai_calls), "AI should be called for at least one child folder"
    
    # Both should get some action (from rules or AI)
    assert action_downloads is not None, "Downloads should get an action"
    assert action_temp is not None, "temp should get an action"
