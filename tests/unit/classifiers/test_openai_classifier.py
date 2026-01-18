"""Tests for OpenAI classifier."""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
from app.classifiers.openai import OpenAIClassifier
from app.folder_action import FolderAction, FolderActionRequest
from app.classifiers.base import FolderActionResponse


def test_openai_classifier_init():
    """Test OpenAIClassifier initialization."""
    classifier = OpenAIClassifier(
        url="https://api.openai.com",
        model="gpt-3.5-turbo",
        api_key="test-key"
    )
    
    assert classifier.url == "https://api.openai.com"
    assert classifier.model == "gpt-3.5-turbo"
    assert classifier.api_key == "test-key"
    assert classifier.is_ai()


def test_openai_folder_action_empty():
    """Test folder action with empty folder."""
    classifier = OpenAIClassifier(
        url="https://api.openai.com",
        model="gpt-3.5-turbo",
    )
    
    request = FolderActionRequest(
        folder_path="/empty",
        folder_name="empty",
        children=[],
        total_files=0,
        rule_hint=None,
    )
    
    response = classifier.advise_folder_action(request)
    assert isinstance(response, FolderActionResponse)
    assert response.is_final
    assert response.action == FolderAction.KEEP


def test_openai_folder_action_with_hint():
    """Test folder action uses hint when folder is empty."""
    classifier = OpenAIClassifier(
        url="https://api.openai.com",
        model="gpt-3.5-turbo",
    )
    
    request = FolderActionRequest(
        folder_path="/test",
        folder_name="test",
        children=[],
        total_files=0,
        rule_hint=FolderAction.DISAGGREGATE,
    )
    
    response = classifier.advise_folder_action(request)
    assert isinstance(response, FolderActionResponse)
    assert response.is_final
    assert response.action == FolderAction.DISAGGREGATE
    assert "empty_folder" in response.reason


def test_openai_custom_folder_prompt():
    """Test OpenAIClassifier with custom folder prompt."""
    custom_prompt = "Custom prompt for testing"
    
    classifier = OpenAIClassifier(
        url="https://api.openai.com",
        model="gpt-3.5-turbo",
        folder_prompt_template=custom_prompt,
    )
    
    assert classifier.folder_prompt_template == custom_prompt
