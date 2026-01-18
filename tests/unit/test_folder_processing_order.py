"""Test that folders are processed in correct order (parent before children) and no duplicate calls."""
import pytest
from unittest.mock import Mock, MagicMock
from app.folder_policy import build_folder_action_map, FolderSample
from app.folder_action import FolderAction, FolderActionRequest
from app.classifiers.base import FolderActionResponse


def test_folders_processed_parent_before_children():
    """Verify folders are processed in order: foo → foo/bar → foo/bar/baz"""
    # Track order of calls
    call_order = []
    
    def mock_advise(request: FolderActionRequest) -> FolderActionResponse:
        call_order.append(request.folder_path)
        # All folders disaggregate
        return FolderActionResponse(
            action=FolderAction.DISAGGREGATE,
            is_final=True,
            reason="test"
        )
    
    mock_classifier = Mock()
    mock_classifier.advise_folder_action = mock_advise
    
    # Create nested folder structure
    samples = {
        "/root/foo": FolderSample(),
        "/root/foo/bar": FolderSample(),
        "/root/foo/bar/baz": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = Mock(return_value=FolderActionResponse(
        action=None, is_final=False, hint=FolderAction.DISAGGREGATE, reason="delegate"
    ))
    
    actions, decisions = build_folder_action_map(mock_rules, mock_classifier, samples, ["/root"])
    
    # Verify order: parent processed before children
    assert call_order == ["/foo", "/foo/bar", "/foo/bar/baz"], f"Wrong order: {call_order}"
    # All folders should be in the map (explicitly tracked)
    assert len(actions) == 3
    assert actions["/root/foo"] == FolderAction.DISAGGREGATE
    assert actions["/root/foo/bar"] == FolderAction.DISAGGREGATE
    assert actions["/root/foo/bar/baz"] == FolderAction.DISAGGREGATE


def test_no_ai_call_for_kept_parent_children():
    """When parent folder is KEPT, children should NOT be classified."""
    call_order = []
    
    def mock_advise(request: FolderActionRequest) -> FolderActionResponse:
        call_order.append(request.folder_path)
        # First folder (parent) is kept
        if request.folder_path == "/foo":
            return FolderActionResponse(
                action=FolderAction.KEEP,
                is_final=True,
                reason="keep_parent"
            )
        # Should never reach here for children
        return FolderActionResponse(
            action=FolderAction.DISAGGREGATE,
            is_final=True,
            reason="should_not_happen"
        )
    
    mock_classifier = Mock()
    mock_classifier.advise_folder_action = mock_advise
    
    samples = {
        "/root/foo": FolderSample(),
        "/root/foo/bar": FolderSample(),
        "/root/foo/bar/baz": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = Mock(return_value=FolderActionResponse(
        action=None, is_final=False, hint=FolderAction.KEEP, reason="delegate"
    ))
    
    actions, decisions = build_folder_action_map(mock_rules, mock_classifier, samples, ["/root"])
    
    # Only parent should be processed
    assert call_order == ["/foo"], f"Children were processed: {call_order}"
    assert len(actions) == 1
    assert actions["/root/foo"] == FolderAction.KEEP
    # Children not in actions map
    assert "/root/foo/bar" not in actions
    assert "/root/foo/bar/baz" not in actions


def test_middle_folder_kept_stops_deeper_nesting():
    """If middle folder kept, deeper children not processed."""
    call_order = []
    
    def mock_advise(request: FolderActionRequest) -> FolderActionResponse:
        call_order.append(request.folder_path)
        if request.folder_path == "/foo/bar":
            return FolderActionResponse(action=FolderAction.KEEP, is_final=True, reason="keep_middle")
        return FolderActionResponse(action=FolderAction.DISAGGREGATE, is_final=True, reason="disaggregate")
    
    mock_classifier = Mock()
    mock_classifier.advise_folder_action = mock_advise
    
    samples = {
        "/root/foo": FolderSample(),
        "/root/foo/bar": FolderSample(),
        "/root/foo/bar/baz": FolderSample(),
        "/root/foo/bar/baz/qux": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = Mock(return_value=FolderActionResponse(
        action=None, is_final=False, hint=None, reason="delegate"
    ))
    
    actions, decisions = build_folder_action_map(mock_rules, mock_classifier, samples, ["/root"])
    
    # Should process: /foo, /foo/bar (kept), but NOT /foo/bar/baz or /foo/bar/baz/qux
    assert call_order == ["/foo", "/foo/bar"], f"Wrong processing: {call_order}"
    assert len(actions) == 2
    assert actions["/root/foo"] == FolderAction.DISAGGREGATE
    assert actions["/root/foo/bar"] == FolderAction.KEEP


def test_no_duplicate_calls_for_same_folder():
    """Each folder should be classified at most once."""
    call_counts = {}
    
    def mock_advise(request: FolderActionRequest) -> FolderActionResponse:
        path = request.folder_path
        call_counts[path] = call_counts.get(path, 0) + 1
        return FolderActionResponse(action=FolderAction.DISAGGREGATE, is_final=True, reason="test")
    
    mock_classifier = Mock()
    mock_classifier.advise_folder_action = mock_advise
    
    samples = {
        "/root/a": FolderSample(),
        "/root/b": FolderSample(),
        "/root/a/nested": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = Mock(return_value=FolderActionResponse(
        action=None, is_final=False, hint=None, reason="delegate"
    ))
    
    # Call twice - should not duplicate
    build_folder_action_map(mock_rules, mock_classifier, samples, ["/root"])
    
    # Each folder called exactly once
    for path, count in call_counts.items():
        assert count == 1, f"Folder {path} called {count} times"
