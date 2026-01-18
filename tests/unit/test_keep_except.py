"""Test folder action inheritance and exception handling."""
import pytest
from unittest.mock import Mock
from app.folder_policy import build_folder_action_map, FolderSample
from app.folder_action import FolderAction, FolderActionRequest
from app.classifiers.base import FolderActionResponse


def test_exception_subfolder_breaks_inheritance():
    """
    A KEEP_EXCEPT folder allows specific subfolders to be evaluated by AI.
    The rules should delegate the exception folder to AI.
    """
    ai_calls = []
    
    def mock_ai_advise(request: FolderActionRequest) -> FolderActionResponse:
        ai_calls.append(request.folder_path)
        # AI would disaggregate temp folders
        if "temp" in request.folder_path.lower():
            return FolderActionResponse(
                action=FolderAction.DISAGGREGATE,
                is_final=True,
                reason="ai_temp_folder"
            )
        return FolderActionResponse(
            action=FolderAction.KEEP,
            is_final=True,
            reason="ai_default"
        )
    
    def mock_rules_advise(request: FolderActionRequest) -> FolderActionResponse:
        # Check specific folders FIRST
        # Temp folder is explicitly evaluated - delegate to AI
        if "temp" in request.folder_path.lower():
            return FolderActionResponse(
                action=None,
                is_final=False,
                hint=FolderAction.DISAGGREGATE,
                reason="temp_folder_check"
            )
        
        # Main app uses KEEP_EXCEPT to allow subfolder evaluation
        if request.folder_path == "/my_app":
            return FolderActionResponse(
                action=FolderAction.KEEP_EXCEPT,
                is_final=True,
                reason="app_rule"
            )
        
        # Other subfolders delegate
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
        "/apps/my_app": FolderSample(),
        "/apps/my_app/data": FolderSample(),
        "/apps/my_app/temp": FolderSample(),
        "/apps/my_app/temp/cache": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    actions, decisions = build_folder_action_map(mock_rules, mock_ai, samples, ["/apps"])
    
    # AI should be called for all subfolders under KEEP_EXCEPT (not just temp)
    assert "/my_app/temp" in ai_calls, f"AI should be called for temp folder, but was called for: {ai_calls}"
    # data folder will also be evaluated since parent is KEEP_EXCEPT
    
    # Main app uses KEEP_EXCEPT
    assert actions["/apps/my_app"] == FolderAction.KEEP_EXCEPT
    
    # Temp folder gets disaggregated by AI
    assert actions["/apps/my_app/temp"] == FolderAction.DISAGGREGATE
    
    # data folder gets kept by AI
    assert actions["/apps/my_app/data"] == FolderAction.KEEP
    
    # cache is under DISAGGREGATE parent, so also evaluated
    assert actions["/apps/my_app/temp/cache"] == FolderAction.DISAGGREGATE


def test_multiple_exception_subfolders():
    """Multiple subfolders can have different actions under a KEEP_EXCEPT parent."""
    
    def mock_rules_advise(request: FolderActionRequest) -> FolderActionResponse:
        path_parts = request.folder_path.strip("/").split("/")
        if not path_parts or path_parts == ['']:
            path_parts = []
        
        # Check exceptions FIRST (before generic rules)
        # Exception folders are explicitly disaggregated
        if any(part in ["node_modules", "build", ".git"] for part in path_parts):
            return FolderActionResponse(
                action=FolderAction.DISAGGREGATE,
                is_final=True,
                reason="build_artifact_rule"
            )
        
        # src folder is kept
        if "src" in path_parts:
            return FolderActionResponse(
                action=FolderAction.KEEP,
                is_final=True,
                reason="src_rule"
            )
        
        # Match root project folder with KEEP_EXCEPT (generic rule)
        if len(path_parts) <= 1:
            return FolderActionResponse(
                action=FolderAction.KEEP_EXCEPT,
                is_final=True,
                reason="project_rule"
            )
        
        return FolderActionResponse(
            action=None,
            is_final=False,
            hint=None,
            reason="no_match"
        )
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = mock_rules_advise
    
    samples = {
        "/project": FolderSample(),
        "/project/src": FolderSample(),
        "/project/node_modules": FolderSample(),
        "/project/node_modules/package": FolderSample(),
        "/project/build": FolderSample(),
        "/project/.git": FolderSample(),
        "/project/.git/objects": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    actions, decisions = build_folder_action_map(mock_rules, None, samples, ["/project"])
    
    # Project root uses KEEP_EXCEPT
    assert actions["/project"] == FolderAction.KEEP_EXCEPT
    
    # Exception folders are disaggregated
    assert actions["/project/node_modules"] == FolderAction.DISAGGREGATE
    assert actions["/project/build"] == FolderAction.DISAGGREGATE
    assert actions["/project/.git"] == FolderAction.DISAGGREGATE
    
    # src folder is kept
    assert actions["/project/src"] == FolderAction.KEEP
    
    # Children of DISAGGREGATE folders are also evaluated and explicitly tracked
    assert actions["/project/node_modules/package"] == FolderAction.DISAGGREGATE
    assert actions["/project/.git/objects"] == FolderAction.DISAGGREGATE


def test_nested_keep_disaggregate_keep():
    """
    Complex case: KEEP_EXCEPT folder -> DISAGGREGATE subfolder -> KEEP folder inside again.
    /app (KEEP_EXCEPT) -> /app/temp (DISAGGREGATE) -> /app/temp/backup_tool (KEEP)
    """
    
    def mock_rules_advise(request: FolderActionRequest) -> FolderActionResponse:
        path_parts = request.folder_path.strip("/").split("/")
        if not path_parts or path_parts == ['']:
            path_parts = []
        
        # Check specific folders FIRST (before generic rules)
        # backup_tool is always kept
        if "backup_tool" in request.folder_path:
            return FolderActionResponse(
                action=FolderAction.KEEP,
                is_final=True,
                reason="tool_rule"
            )
        
        # temp folders are disaggregated
        if "temp" in request.folder_path:
            return FolderActionResponse(
                action=FolderAction.DISAGGREGATE,
                is_final=True,
                reason="temp_folder_rule"
            )
        
        # config folder is kept
        if "config" in request.folder_path:
            return FolderActionResponse(
                action=FolderAction.KEEP,
                is_final=True,
                reason="config_rule"
            )
        
        # Root app folder uses KEEP_EXCEPT (generic rule)
        if len(path_parts) <= 1:
            return FolderActionResponse(
                action=FolderAction.KEEP_EXCEPT,
                is_final=True,
                reason="app_rule"
            )
        
        return FolderActionResponse(
            action=None,
            is_final=False,
            hint=None,
            reason="no_match"
        )
    
    mock_rules = Mock()
    mock_rules.advise_folder_action = mock_rules_advise
    
    samples = {
        "/app": FolderSample(),
        "/app/config": FolderSample(),
        "/app/temp": FolderSample(),
        "/app/temp/cache": FolderSample(),
        "/app/temp/backup_tool": FolderSample(),
        "/app/temp/backup_tool/data": FolderSample(),
    }
    for sample in samples.values():
        sample.total_files = 1
        sample.children = {"file.txt": {"type": "file", "mime": "text/plain", "size": 100, "file_count": 1}}
    
    actions, decisions = build_folder_action_map(mock_rules, None, samples, ["/app"])
    
    # App uses KEEP_EXCEPT
    assert actions["/app"] == FolderAction.KEEP_EXCEPT
    
    # Temp is disaggregated
    assert actions["/app/temp"] == FolderAction.DISAGGREGATE
    
    # backup_tool inside temp is kept (new decision point)
    assert actions["/app/temp/backup_tool"] == FolderAction.KEEP
    
    # config is kept by rule
    assert actions["/app/config"] == FolderAction.KEEP
    
    # cache gets DISAGGREGATE from rule (same as parent but explicitly tracked)
    assert actions["/app/temp/cache"] == FolderAction.DISAGGREGATE
    
    # data inherits KEEP from backup_tool (not evaluated due to parent KEEP)
    assert "/app/temp/backup_tool/data" not in actions  # Inherits KEEP from backup_tool
