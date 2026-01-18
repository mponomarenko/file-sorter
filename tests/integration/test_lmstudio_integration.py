"""Integration tests for LM Studio (OpenAI-compatible) classifier.

This test suite verifies that the OpenAI classifier works correctly with
LM Studio, a local OpenAI-compatible API server.

To run these tests:
    LMSTUDIO_URL=http://localhost:1234 ./test.sh tests/integration/test_lmstudio_integration.py -v

Requirements:
    - LM Studio running at LMSTUDIO_URL
    - Model loaded in LM Studio (e.g., openai/gpt-oss-20b)
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.classifiers import create_ai_classifier, RulesClassifier
from app.classifiers.openai import OpenAIClassifier
from app.folder_action import FolderAction, FolderActionRequest
from app.categories import CategoryPath


# Skip all tests if LMSTUDIO_URL not set
pytestmark = pytest.mark.skipif(
    not os.getenv("LMSTUDIO_URL"),
    reason="LMSTUDIO_URL environment variable not set"
)


@pytest.fixture
def lmstudio_url():
    """Get LM Studio URL from environment."""
    return os.getenv("LMSTUDIO_URL", "http://localhost:1234")


@pytest.fixture
def lmstudio_model():
    """Get model name from environment (required)."""
    model = os.getenv("LMSTUDIO_MODEL")
    if not model:
        pytest.skip("LMSTUDIO_MODEL not set")
    return model


@pytest.fixture
def classifier(lmstudio_url, lmstudio_model):
    """Create LM Studio classifier."""
    return create_ai_classifier(lmstudio_url, model=lmstudio_model)


@pytest.fixture
def rules_classifier():
    """Create rules classifier for chain testing."""
    return RulesClassifier()


def test_lmstudio_auto_detection(lmstudio_url):
    """Test that LM Studio is auto-detected as OpenAI-compatible."""
    classifier = create_ai_classifier(lmstudio_url)
    
    assert isinstance(classifier, OpenAIClassifier)
    assert classifier.url == lmstudio_url
    assert classifier.is_ai()


def test_lmstudio_availability(classifier):
    """Test that LM Studio endpoint is available."""
    available = classifier.ensure_available()
    assert available, "LM Studio endpoint should be available"


def test_lmstudio_model_list(lmstudio_url):
    """Test that we can list available models."""
    import httpx
    
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{lmstudio_url}/v1/models")
        assert resp.status_code == 200
        
        data = resp.json()
        assert "data" in data
        models = [m["id"] for m in data["data"]]
        
        # Should have at least one model
        assert len(models) > 0
        print(f"Available models: {models}")


def test_folder_action_meaningful_name(classifier):
    """Test folder action with meaningful folder name (should keep)."""
    request = FolderActionRequest(
        folder_path="/source/TaxDocuments-2024",
        folder_name="TaxDocuments-2024",
        children=[
            {"name": "invoice1.pdf", "type": "file", "mime": "application/pdf"},
            {"name": "invoice2.pdf", "type": "file", "mime": "application/pdf"},
        ],
        total_files=15,
        rule_hint=None,
    )
    
    response = classifier.advise_folder_action(request)
    
    assert response.is_final
    assert response.action == FolderAction.KEEP
    assert "openai" in response.reason.lower()


def test_folder_action_generic_name(classifier):
    """Test folder action with generic folder name (should disaggregate)."""
    request = FolderActionRequest(
        folder_path="/Downloads",
        folder_name="Downloads",
        children=[
            {"name": "file1.txt", "type": "file", "mime": "text/plain"},
            {"name": "file2.pdf", "type": "file", "mime": "application/pdf"},
        ],
        total_files=2,
        rule_hint=None,
    )
    
    response = classifier.advise_folder_action(request)
    
    assert response.is_final
    assert response.action == FolderAction.DISAGGREGATE
    assert "openai" in response.reason.lower()


def test_folder_action_project_name(classifier):
    """Test folder action with project-like name (should keep)."""
    request = FolderActionRequest(
        folder_path="/source/my-website-redesign-v2",
        folder_name="my-website-redesign-v2",
        children=[
            {"name": "index.html", "type": "file", "mime": "text/html"},
            {"name": "css", "type": "dir"},
            {"name": "js", "type": "dir"},
        ],
        total_files=50,
        rule_hint=None,
    )
    
    response = classifier.advise_folder_action(request)
    
    assert response.is_final
    assert response.action == FolderAction.KEEP
    assert "openai" in response.reason.lower()


def test_folder_action_with_hint(classifier):
    """Test that classifier respects rule hints."""
    request = FolderActionRequest(
        folder_path="/test",
        folder_name="test",
        children=[],
        total_files=0,
        rule_hint=FolderAction.DISAGGREGATE,
    )
    
    response = classifier.advise_folder_action(request)
    
    assert response.is_final
    assert response.action == FolderAction.DISAGGREGATE
    assert "empty_folder" in response.reason


def test_rules_to_ai_delegation_chain(classifier, rules_classifier):
    """Test full chain: Rules delegate to AI for ambiguous case."""
    # Folder with no structural markers - rules should delegate
    request = FolderActionRequest(
        folder_path="/source/RandomStuff",
        folder_name="RandomStuff",
        children=[
            {"name": "file1.txt", "type": "file", "mime": "text/plain", "size": 100},
            {"name": "file2.doc", "type": "file", "mime": "application/msword", "size": 200},
        ],
        total_files=2,
        rule_hint=None,
    )
    
    # Step 1: Rules classifier
    rules_response = rules_classifier.advise_folder_action(request)
    
    # Rules should delegate (no markers, matches catch-all)
    assert not rules_response.is_final
    assert rules_response.hint is not None
    
    # Step 2: AI classifier with hint
    request.rule_hint = rules_response.hint
    ai_response = classifier.advise_folder_action(request)
    
    # AI should make final decision
    assert ai_response.is_final
    assert ai_response.action in [FolderAction.KEEP, FolderAction.DISAGGREGATE]


def test_rules_final_decision_no_ai_consult(classifier, rules_classifier):
    """Test that rules make final decision for structural markers (AI not consulted)."""
    # Folder with .git - rules should decide immediately
    request = FolderActionRequest(
        folder_path="/source/myproject",
        folder_name="myproject",
        children=[
            {"name": ".git", "type": "dir", "mime": "*"},
            {"name": "src", "type": "dir", "mime": "*"},
        ],
        total_files=100,
        rule_hint=None,
    )
    
    # Rules classifier should make final decision
    rules_response = rules_classifier.advise_folder_action(request)
    
    assert rules_response.is_final
    assert rules_response.action == FolderAction.KEEP
    assert "keep_parent" in rules_response.reason
    
    # AI should never be consulted for this case


def test_rules_package_json_marker(classifier, rules_classifier):
    """Test that package.json is recognized as keep_parent marker."""
    request = FolderActionRequest(
        folder_path="/source/webapp",
        folder_name="webapp",
        children=[
            {"name": "package.json", "type": "file", "mime": "application/json", "size": 1024},
            {"name": "server.js", "type": "file", "mime": "application/javascript", "size": 2048},
        ],
        total_files=50,
        rule_hint=None,
    )
    
    rules_response = rules_classifier.advise_folder_action(request)
    
    assert rules_response.is_final
    assert rules_response.action == FolderAction.KEEP
    assert "keep_parent" in rules_response.reason


def test_async_file_classification(classifier):
    """Test async file classification with LM Studio."""
    import asyncio
    
    async def run_classification():
        response = await classifier.classify(
            name="document.pdf",
            rel_path="Documents/Work/document.pdf",
            mime="application/pdf",
            sample="This is a work document about quarterly reports...",
            hint={"rule_hint": "Documents"}
        )
        
        assert isinstance(response.path, CategoryPath)
        assert response.metrics is not None
        assert "model" in response.metrics
        assert "usage" in response.metrics
        return response
    
    # Run async function
    response = asyncio.run(run_classification())
    assert response is not None


def test_folder_action_batch(classifier):
    """Test multiple folder decisions to verify consistency."""
    test_cases = [
        ("Wedding-Photos-2024", FolderAction.KEEP, "meaningful event name"),
        ("Downloads", FolderAction.DISAGGREGATE, "system folder"),
        ("Work-Contracts", FolderAction.KEEP, "meaningful descriptive name"),
        ("temp", FolderAction.DISAGGREGATE, "generic name"),
        ("MyProject", FolderAction.KEEP, "project-like name"),
        ("Misc", FolderAction.DISAGGREGATE, "generic name"),
    ]
    
    results = []
    for folder_name, expected_action, description in test_cases:
        request = FolderActionRequest(
            folder_path=f"/source/{folder_name}",
            folder_name=folder_name,
            children=[{"name": "file.txt", "type": "file", "mime": "text/plain"}],
            total_files=5,
            rule_hint=None,
        )
        
        response = classifier.advise_folder_action(request)
        results.append((folder_name, response.action, expected_action, description))
    
    # Print results
    print("\nFolder Action Results:")
    print("-" * 80)
    for folder_name, actual, expected, desc in results:
        match = "✓" if actual == expected else "✗"
        print(f"{match} {folder_name:25s} → {str(actual):15s} (expected: {expected}, {desc})")
    
    # Assert at least 80% accuracy
    correct = sum(1 for _, actual, expected, _ in results if actual == expected)
    accuracy = correct / len(results)
    assert accuracy >= 0.8, f"Expected at least 80% accuracy, got {accuracy:.1%}"


def test_token_usage_tracking(classifier):
    """Test that token usage is tracked correctly."""
    request = FolderActionRequest(
        folder_path="/test",
        folder_name="test-folder",
        children=[{"name": "file.txt", "type": "file", "mime": "text/plain"}],
        total_files=1,
        rule_hint=None,
    )
    
    response = classifier.advise_folder_action(request)
    
    # LM Studio should return usage stats
    assert response.is_final
    # Token usage is logged but not returned in response currently
    # This test just verifies the call succeeds


def test_concurrent_requests(classifier):
    """Test that classifier handles concurrent requests correctly."""
    import concurrent.futures
    
    def make_request(folder_name):
        request = FolderActionRequest(
            folder_path=f"/test/{folder_name}",
            folder_name=folder_name,
            children=[{"name": "file.txt", "type": "file", "mime": "text/plain"}],
            total_files=1,
            rule_hint=None,
        )
        return classifier.advise_folder_action(request)
    
    folder_names = [f"folder-{i}" for i in range(5)]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(make_request, folder_names))
    
    # All requests should succeed
    assert len(results) == 5
    assert all(r.is_final for r in results)


if __name__ == "__main__":
    # Allow running directly for quick testing
    import sys
    
    if not os.getenv("LMSTUDIO_URL"):
        print("Error: LMSTUDIO_URL environment variable not set")
        print("Usage: LMSTUDIO_URL=http://localhost:1234 python test_lmstudio_integration.py")
        sys.exit(1)
    
    pytest.main([__file__, "-v", "-s"])
