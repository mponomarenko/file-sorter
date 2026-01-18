"""Tests for AI auto-detection."""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
from app.classifiers.ai_auto import (
    _detect_from_url_pattern,
    _instantiate_classifier,
    create_ai_classifier,
    clear_cache,
)
from app.classifiers.ollama import OllamaClassifier
from app.classifiers.openai import OpenAIClassifier


def test_detect_ollama_from_url():
    """Test Ollama detection from URL patterns."""
    assert _detect_from_url_pattern("http://localhost:11434") == OllamaClassifier
    assert _detect_from_url_pattern("http://ollama.local") == OllamaClassifier
    assert _detect_from_url_pattern("http://server/api/tags") == OllamaClassifier


def test_detect_openai_from_url():
    """Test OpenAI detection from URL patterns."""
    assert _detect_from_url_pattern("https://api.openai.com") == OpenAIClassifier
    assert _detect_from_url_pattern("https://api.groq.com/v1/") == OpenAIClassifier
    assert _detect_from_url_pattern("https://azure.openai.com") == OpenAIClassifier


def test_detect_unknown_url():
    """Test detection with unknown URL."""
    assert _detect_from_url_pattern("http://unknown.server") is None


def test_instantiate_ollama():
    """Test instantiating Ollama classifier."""
    classifier = _instantiate_classifier(
        OllamaClassifier,
        url="http://localhost:11434",
        api_key=None,
        model="llama3.1",
        max_concurrency=5,
        file_prompt_template=None,
        folder_prompt_template=None,
        extra_kwargs={},
    )
    
    assert isinstance(classifier, OllamaClassifier)
    assert classifier.url == "http://localhost:11434"


def test_instantiate_openai():
    """Test instantiating OpenAI classifier."""
    classifier = _instantiate_classifier(
        OpenAIClassifier,
        url="https://api.openai.com",
        api_key="test-key",
        model="gpt-4",
        max_concurrency=5,
        file_prompt_template=None,
        folder_prompt_template=None,
        extra_kwargs={},
    )
    
    assert isinstance(classifier, OpenAIClassifier)
    assert classifier.url == "https://api.openai.com"
    assert classifier.model == "gpt-4"
    assert classifier.api_key == "test-key"


def test_instantiate_openai_auto_model():
    """Test OpenAI classifier with auto-detected model."""
    classifier = _instantiate_classifier(
        OpenAIClassifier,
        url="https://api.openai.com",
        api_key="test-key",
        model=None,  # Should auto-detect
        max_concurrency=5,
        file_prompt_template=None,
        folder_prompt_template=None,
        extra_kwargs={},
    )
    
    assert classifier.model == "gpt-3.5-turbo"  # Default


@pytest.mark.skip(reason="Requires actual endpoint to test")
def test_create_ai_classifier_from_pattern():
    """Test create_ai_classifier with URL pattern detection."""
    clear_cache()
    
    # Should detect Ollama from URL
    classifier = create_ai_classifier("http://localhost:11434")
    assert isinstance(classifier, OllamaClassifier)


@pytest.mark.skip(reason="Requires actual endpoint to test")
def test_cache_behavior():
    """Test that endpoint type is cached."""
    clear_cache()
    
    url = "http://test-ollama-url:11434"
    
    # First call should detect and cache
    classifier1 = create_ai_classifier(url)
    assert isinstance(classifier1, OllamaClassifier)
    
    # Second call should use cache
    classifier2 = create_ai_classifier(url)
    assert isinstance(classifier2, OllamaClassifier)
    
    clear_cache()
