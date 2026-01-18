"""Mock AI classifier for testing that uses exact path matching.

This classifier implements the full Classifier protocol and can be used in tests
to simulate AI decisions without actually calling an AI service.
"""

from pathlib import PurePosixPath
from typing import Dict, Any

from ..categories import CategoryPath, UNKNOWN_CATEGORY
from ..folder_action import FolderAction, FolderActionRequest
from .base import Classifier, ClassifierResponse, FolderActionResponse


class MockAIClassifier(Classifier):
    """Mock AI classifier that supports both file classification and folder actions.
    
    This classifier is intended for testing. It allows tests to configure:
    - Which folders should be kept vs disaggregated (using relative paths)
    - Which files should be classified to specific categories (using path patterns)
    
    Example:
        mock = MockAIClassifier()
        mock.set_keep_folders([
            "/Music/Artist",
            "/Music/Artist/Album",
        ])
        mock.set_file_classifications({
            "document.pdf": CategoryPath("Documents", "General"),
            "song.mp3": CategoryPath("Media", "Music"),
        })
    """
    
    def __init__(self):
        """Initialize the mock classifier."""
        self._keep_folders: set[str] = set()
        self._file_classifications: dict[str, CategoryPath] = {}
        self._default_category: CategoryPath = UNKNOWN_CATEGORY
    
    def set_keep_folders(self, paths: list[str]) -> None:
        """Set the exact folder paths (relative, after source stripping) that should be kept.
        
        Note: folder_policy strips source prefixes before calling this classifier,
        so paths should be relative (e.g., "/Music/Artist" not "/mnt/storage/Music/Artist").
        
        Args:
            paths: List of relative folder paths that should return "move_as_unit".
                  All other paths will return "disaggregate".
        """
        self._keep_folders = set(paths)
    
    def set_file_classifications(self, classifications: dict[str, CategoryPath]) -> None:
        """Set file classification mappings.
        
        Files will be matched by checking if the key appears in the rel_path.
        For example, "document.pdf" will match any path containing "document.pdf".
        
        Args:
            classifications: Dictionary mapping path patterns to category paths
        """
        self._file_classifications = dict(classifications)
    
    def set_default_category(self, category: CategoryPath) -> None:
        """Set the default category for files that don't match any classification.
        
        Args:
            category: Default category path to use
        """
        self._default_category = category
    
    def advise_folder_action(self, request: FolderActionRequest) -> FolderActionResponse:
        """Advise whether to keep or disaggregate a folder.
        
        AI classifiers always make final decisions (never delegate).
        Uses hint from previous classifier as guidance if available.
        """
        
        if request.folder_path in self._keep_folders:
            return FolderActionResponse.decision(FolderAction.KEEP, reason="mock:configured")
        
        # Use hint if provided, otherwise disaggregate
        return FolderActionResponse.decision(
            request.rule_hint or FolderAction.DISAGGREGATE,
            reason="mock:using_hint" if request.rule_hint else "mock:default"
        )
    
    async def classify(
        self,
        name: str,
        rel_path: str,
        mime: str,
        sample: str,
        hint: dict | None = None,
    ) -> ClassifierResponse:
        """Classify a file based on configured classifications.
        
        Checks if any configured pattern matches the rel_path. If multiple patterns
        match, uses the longest match. Falls back to default category if no match.
        
        Args:
            name: File name
            rel_path: Relative path
            mime: MIME type
            sample: File content sample
            hint: Optional hint dictionary
            
        Returns:
            ClassifierResponse with matched category or default category
        """
        # Find matching classification (longest match wins)
        best_match = None
        best_match_len = -1
        
        for pattern, category in self._file_classifications.items():
            if pattern in rel_path and len(pattern) > best_match_len:
                best_match = category
                best_match_len = len(pattern)
        
        category = best_match if best_match else self._default_category
        
        return ClassifierResponse(
            path=category,
            metrics={
                "source": "mock",
                "mock": True,
                "pattern_matched": best_match_len > -1,
                "match_length": best_match_len if best_match_len > -1 else 0,
            },
        )
    
    async def close(self):
        """Close classifier resources (no-op for mock)."""
        pass
    
    def ensure_available(self) -> bool:
        """Check if classifier is available (always True for mock)."""
        return True
    
    def display_name(self) -> str:
        """Get display name for classifier."""
        return "mock_ai_classifier"
    
    def is_ai(self) -> bool:
        """Check if this is an AI classifier."""
        return True
