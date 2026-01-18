"""Unit tests for CLI folder action decisions with mock AI."""
import pytest
from pathlib import Path
import sys

# Add parent to path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.classifiers import RulesClassifier
from app.classifiers.mock import MockAIClassifier
from app.categories import CategoryPath
from app.folder_action import FolderAction
from cli.cli_shared import build_folder_actions_for_path


class TestCLIFolderActions:
    """Test CLI folder action logic with mock AI classifier."""
    
    def test_cli_walks_folders_and_calls_ai(self):
        """CLI should walk up from file and call AI on each folder."""
        # Setup
        cfg = AppConfig.from_env()
        rules = RulesClassifier()
        
        # Mock AI that returns disaggregate for everything (all folders not in keep list)
        mock_ai = MockAIClassifier()
        mock_ai.set_default_category(CategoryPath("Documents", "Other"))
        
        # Test path: fixtures/paths/Dropbox/Demos/Example-Project/Example-Document.docx
        test_root = "fixtures/paths"
        test_path = f"{test_root}/Dropbox/Demos/Example-Project/Example-Document.docx"
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        file_size = 21402
        
        # Expected folders to be checked (from deepest to root):
        expected_folders = [
            "/fixtures/paths/Dropbox/Demos/Example-Project",
            "/fixtures/paths/Dropbox/Demos",
            "/fixtures/paths/Dropbox",  # Has rule: disaggregate,final
            "/fixtures/paths",
            "/fixtures",
        ]
        
        # Call the shared CLI logic
        folder_actions, folder_decisions, folder_details = build_folder_actions_for_path(
            test_path,
            mime,
            file_size,
            rules,
            mock_ai,
            cfg.SOURCES,
        )
    
        # Verify all expected folders were processed
        assert len(folder_actions) == len(expected_folders), \
            f"Expected {len(expected_folders)} folders, got {len(folder_actions)}.\nGot: {list(folder_actions.keys())}"
        
        for folder in expected_folders:
            assert folder in folder_actions, f"Folder {folder} not in actions map"
        
        # Verify Dropbox has disaggregate (from rule)
        assert folder_actions["/fixtures/paths/Dropbox"] == FolderAction.DISAGGREGATE
        
        # Verify other folders got decisions from mock AI
        assert folder_actions["/fixtures/paths/Dropbox/Demos"] == FolderAction.DISAGGREGATE
        assert folder_actions["/fixtures/paths/Dropbox/Demos/Example-Project"] == FolderAction.DISAGGREGATE


    def test_system_folders_get_ai_decision(self):
        """Test that system-like folders get AI decisions."""
        cfg = AppConfig.from_env()
        rules = RulesClassifier()
        
        # Mock AI that returns disaggregate for system folders (default behavior)
        mock_ai = MockAIClassifier()
        mock_ai.set_default_category(CategoryPath("Documents", "Other"))
        
        # Test various system folder paths
        system_paths = [
            "fixtures/paths/system/Users/file.txt",
            "fixtures/paths/system/somedir/file.txt",
            "fixtures/paths/system/storage/data/file.txt",
        ]
        
        for test_path in system_paths:
            folder_actions, folder_decisions, folder_details = build_folder_actions_for_path(
                test_path,
                "text/plain",
                1024,
                rules,
                mock_ai,
                cfg.SOURCES,
            )
            
            # All intermediate system folders should get decisions
            for folder_path, action in folder_actions.items():
                # Mock returns "disaggregate" by default (not in keep list)
                assert action == FolderAction.DISAGGREGATE, \
                    f"Expected disaggregate for {folder_path} in path {test_path}, got {action}"


    def test_dropbox_rule_is_final(self):
        """Test that Dropbox rule is final and doesn't call AI."""
        cfg = AppConfig.from_env()
        rules = RulesClassifier()
        
        from app.folder_action import RequiresAI
        
        # Check Dropbox rule
        match = rules.match("", "/fixtures/paths/Dropbox", "")
        
        assert match is not None, "Dropbox should match a rule"
        assert match.rule.folder_action is not None, "Dropbox rule should have folder_action"
        assert match.rule.folder_action.value == "disaggregate", "Dropbox should be disaggregate"
        assert match.rule.requires_ai == RequiresAI.FINAL, "Dropbox rule should be final (not call AI)"


    def test_folder_sample_shows_direct_children_only(self):
        """Test that FolderSample only shows direct children, not deep paths."""
        from app.folder_policy import FolderSample
        
        sample = FolderSample()
        
        # Add a file child
        sample.add_child("file.txt", is_dir=False, mime="text/plain", size=1024)
        
        # Add a directory child
        sample.add_child("subdir", is_dir=True, mime="*", size=0)
        sample.total_files = 5  # Say there are 5 files total in the tree
        
        payload = sample.payload("/test/folder")
        
        # Check payload structure
        assert payload["folder_path"] == "/test/folder"
        assert payload["total_files"] == 5
        assert payload["child_count"] == 2
        assert len(payload["children"]) == 2
        
        # Check children are properly formatted
        file_child = next(c for c in payload["children"] if c["name"] == "file.txt")
        assert file_child["type"] == "file"
        assert file_child["mime"] == "text/plain"
        assert file_child["size"] == 1024
        
        dir_child = next(c for c in payload["children"] if c["name"] == "subdir")
        assert dir_child["type"] == "dir"
        assert "files_inside" in dir_child  # Shows count of files in subdir


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
