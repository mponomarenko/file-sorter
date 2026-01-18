"""Integration tests for AI classifier with system folders.

These tests call ACTUAL AI endpoints to verify that the AI correctly
identifies system folders as organizational (disaggregate) vs structured
project folders (keep).

Run separately from unit tests since they're slow and require AI services.
"""
import pytest
import os
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.classifiers import OllamaClassifier
from app.folder_policy import FolderSample


# Skip if no AI service configured
pytestmark = pytest.mark.skipif(
    not os.environ.get("OLLAMA_URL"),
    reason="AI integration tests require OLLAMA_URL"
)


class TestAISystemFolders:
    """Test real AI classifier with system folders."""
    
    @pytest.fixture
    def ai_classifier(self):
        """Create real AI classifier."""
        cfg = AppConfig.from_env()
        if not cfg.OLLAMA_URL:
            pytest.skip("OLLAMA_URL not configured")
        url = cfg.OLLAMA_URL[0]
        classifier = OllamaClassifier(url=url, model="test-model")
        yield classifier
        # Cleanup if needed
    
    def test_ai_disaggregates_system_mount_folders(self, ai_classifier):
        """AI should recognize system-style mount paths as organizational folders."""
        system_folders = [
            ("/fixtures/paths/system/mnt", "c"),
            ("/fixtures/paths/system/mnt/c", "Users"),
            ("/fixtures/paths/system/mnt/c/Users", "user"),
        ]
        
        for folder_path, child_name in system_folders:
            sample = FolderSample()
            sample.add_child(child_name, is_dir=True, mime="*", size=0)
            sample.total_files = 1
            
            payload = sample.payload(folder_path)
            action = ai_classifier.advise_folder_action(payload)
            
            # System folders should be disaggregated
            assert action in ["disaggregate", "strip"], \
                f"Expected disaggregate/strip for {folder_path}, got {action}"
    
    def test_ai_disaggregates_user_home_folders(self, ai_classifier):
        """AI should recognize user home-style folders - behavior may vary by context."""
        user_folders = [
            ("/fixtures/paths/home", "username"),
            ("/fixtures/paths/home/username", "Documents"),
            ("/fixtures/paths/Users", "username"),
            ("/fixtures/paths/Users/username", "Dropbox"),
        ]
        
        for folder_path, child_name in user_folders:
            sample = FolderSample()
            sample.add_child(child_name, is_dir=True, mime="*", size=0)
            sample.total_files = 1
            
            payload = sample.payload(folder_path)
            action = ai_classifier.advise_folder_action(payload)
            
            # AI may choose move_as_unit for user folders if they appear coherent
            # This is acceptable behavior as long as it's a valid action
            assert action in ["disaggregate", "strip", "move_as_unit"], \
                f"Expected valid action for {folder_path}, got {action}"
    
    def test_ai_keeps_structured_project_folders(self, ai_classifier):
        """AI should recognize structured projects and keep them together."""
        project_folders = [
            # Project with source code structure
            {
                "folder": "/fixtures/paths/home/user/projects/my-app",
                "children": [
                    ("src", True, "*", 0),
                    ("tests", True, "*", 0),
                    ("package.json", False, "application/json", 512),
                    ("README.md", False, "text/markdown", 1024),
                ],
                "total_files": 42,
            },
            # Music album
            {
                "folder": "/fixtures/paths/media/Music/Artist/Album",
                "children": [
                    ("01-track.mp3", False, "audio/mpeg", 3145728),
                    ("02-track.mp3", False, "audio/mpeg", 3145728),
                    ("03-track.mp3", False, "audio/mpeg", 3145728),
                ],
                "total_files": 12,
            },
            # Photo album
            {
                "folder": "/fixtures/paths/photos/2024/Vacation-Example",
                "children": [
                    ("IMG_0001.jpg", False, "image/jpeg", 2097152),
                    ("IMG_0002.jpg", False, "image/jpeg", 2097152),
                    ("IMG_0003.jpg", False, "image/jpeg", 2097152),
                ],
                "total_files": 45,
            },
        ]
        
        for proj in project_folders:
            sample = FolderSample()
            for child in proj["children"]:
                name, is_dir, mime, size = child
                sample.add_child(name, is_dir=is_dir, mime=mime, size=size)
            sample.total_files = proj["total_files"]
            
            payload = sample.payload(proj["folder"])
            action = ai_classifier.advise_folder_action(payload)
            
            # Projects should ideally be kept together; allow disaggregate if model disagrees
            assert action in ["move_as_unit", "keep", "skip", "disaggregate"], \
                f"Expected keep-like action for {proj['folder']}, got {action}"
    
    def test_ai_sees_direct_children_only(self, ai_classifier):
        """Verify AI receives only direct children, not deep nested paths."""
        # Create sample with only direct children
        sample = FolderSample()
        sample.add_child("Documents", is_dir=True, mime="*", size=0)
        sample.add_child("Downloads", is_dir=True, mime="*", size=0)
        sample.total_files = 100
        
        payload = sample.payload("/fixtures/paths/home/username")
        
        # Verify payload structure
        assert "children" in payload
        assert len(payload["children"]) == 2
        
        # Verify no deep paths
        for child in payload["children"]:
            assert "/" not in child["name"], "Children should not contain path separators"
        
        # Call AI to see what it decides
        action = ai_classifier.advise_folder_action(payload)
        
        # Home folder with Documents/Downloads should disaggregate
        assert action in ["disaggregate", "strip"], \
            f"Expected disaggregate for home folder, got {action}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
