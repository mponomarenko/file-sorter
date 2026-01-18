"""Test that KEEP action is inherited by all subfolders, even with misleading names."""
import pytest
from unittest.mock import Mock
from app.folder_policy import build_folder_action_map, FolderSample
from app.folder_action import FolderAction, FolderActionRequest
from app.classifiers.base import FolderActionResponse


def test_portable_app_with_documents_subfolder_inherits_keep():
    """
    A portable app folder is KEPT, and even if it contains a 'Documents' subfolder,
    that subfolder should inherit KEEP without calling AI.
    """
    ai_calls = []
    
    def mock_ai_advise(request: FolderActionRequest) -> FolderActionResponse:
        ai_calls.append(request.folder_path)
        # AI would normally disaggregate "Documents"
        if "Documents" in request.folder_path:
            return FolderActionResponse(
                action=FolderAction.DISAGGREGATE,
                is_final=True,
                reason="ai_thinks_sync_folder"
            )
        # But should never be called for subfolder
        return FolderActionResponse(
            action=FolderAction.KEEP,
            is_final=True,
            reason="ai_default"
        )
    
    def mock_rules_advise(request: FolderActionRequest) -> FolderActionResponse:
        # Rules recognize portable apps
        if "pidgin_portable" in request.folder_path:
            return FolderActionResponse(
                action=FolderAction.KEEP,
                is_final=True,
                reason="matched_portable_app_rule"
            )
        # Delegate others to AI
        return FolderActionResponse(
            action=None,
            is_final=False,
            hint=None,
            reason="no_rule_match"
        )
    
    mock_ai = Mock()
    mock_ai.advise_folder_action = mock_ai_advise
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = mock_rules_advise
    
    # Create folder structure: pidgin_portable/Data/Documents/file.txt
    samples = {
        "/test/pidgin_portable": FolderSample(),
        "/test/pidgin_portable/Data": FolderSample(),
        "/test/pidgin_portable/Data/Documents": FolderSample(),
        "/test/pidgin_portable/Data/Documents/logs": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    actions, decisions = build_folder_action_map(mock_rules, mock_ai, samples, ["/test"])
    
    # AI should NEVER be called for any subfolder
    assert len(ai_calls) == 0, f"AI was called for: {ai_calls}"
    
    # Only the root portable app should be in actions
    assert len(actions) == 1
    assert actions["/test/pidgin_portable"] == FolderAction.KEEP
    
    # Subfolders not in action map (inherited)
    assert "/test/pidgin_portable/Data" not in actions
    assert "/test/pidgin_portable/Data/Documents" not in actions
    assert "/test/pidgin_portable/Data/Documents/logs" not in actions


def test_system_folder_with_portable_app_subfolder():
    """
    Opposite case: Documents (disaggregate) contains pidgin_portable (keep).
    Both should be classified.
    """
    ai_calls = []
    
    def mock_ai_advise(request: FolderActionRequest) -> FolderActionResponse:
        ai_calls.append(request.folder_path)
        if "Documents" in request.folder_path and "pidgin_portable" not in request.folder_path:
            return FolderActionResponse(
                action=FolderAction.DISAGGREGATE,
                is_final=True,
                reason="ai_sync_folder"
            )
        # Should not reach portable subfolder
        return FolderActionResponse(
            action=FolderAction.KEEP,
            is_final=True,
            reason="ai_fallback"
        )
    
    def mock_rules_advise(request: FolderActionRequest) -> FolderActionResponse:
        if "pidgin_portable" in request.folder_path:
            return FolderActionResponse(
                action=FolderAction.KEEP,
                is_final=True,
                reason="portable_app_rule"
            )
        return FolderActionResponse(
            action=None,
            is_final=False,
            hint=None,
            reason="no_match"
        )
    
    mock_ai = Mock()
    mock_ai.advise_folder_action = mock_ai_advise
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = mock_rules_advise
    
    samples = {
        "/test/Documents": FolderSample(),
        "/test/Documents/pidgin_portable": FolderSample(),
        "/test/Documents/pidgin_portable/Data": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    actions, decisions = build_folder_action_map(mock_rules, mock_ai, samples, ["/test"])
    
    # AI called only for Documents
    assert ai_calls == ["/Documents"], f"Wrong AI calls: {ai_calls}"
    
    # Both Documents and pidgin_portable should have actions
    assert len(actions) == 2
    assert actions["/test/Documents"] == FolderAction.DISAGGREGATE
    assert actions["/test/Documents/pidgin_portable"] == FolderAction.KEEP
    
    # Deep subfolder inherits KEEP
    assert "/test/Documents/pidgin_portable/Data" not in actions


def test_multiple_portable_apps_at_same_level():
    """Multiple sibling folders can all be KEPT independently."""
    ai_calls = []
    
    def mock_ai_advise(request: FolderActionRequest) -> FolderActionResponse:
        ai_calls.append(request.folder_path)
        return FolderActionResponse(
            action=FolderAction.DISAGGREGATE,
            is_final=True,
            reason="ai_default"
        )
    
    def mock_rules_advise(request: FolderActionRequest) -> FolderActionResponse:
        if "_portable" in request.folder_path:
            return FolderActionResponse(
                action=FolderAction.KEEP,
                is_final=True,
                reason="portable_rule"
            )
        return FolderActionResponse(
            action=None,
            is_final=False,
            hint=None,
            reason="no_match"
        )
    
    mock_ai = Mock()
    mock_ai.advise_folder_action = mock_ai_advise
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = mock_rules_advise
    
    samples = {
        "/apps/firefox_portable": FolderSample(),
        "/apps/firefox_portable/profile": FolderSample(),
        "/apps/chrome_portable": FolderSample(),
        "/apps/chrome_portable/data": FolderSample(),
        "/apps/random_folder": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    actions, decisions = build_folder_action_map(mock_rules, mock_ai, samples, ["/apps"])
    
    # AI only called for random_folder
    assert ai_calls == ["/random_folder"], f"Wrong AI calls: {ai_calls}"
    
    # Portable apps are kept
    assert actions["/apps/firefox_portable"] == FolderAction.KEEP
    assert actions["/apps/chrome_portable"] == FolderAction.KEEP
    assert actions["/apps/random_folder"] == FolderAction.DISAGGREGATE
    
    # Subfolders not in map
    assert "/apps/firefox_portable/profile" not in actions
    assert "/apps/chrome_portable/data" not in actions
