"""Auto-detection factory for AI classifiers.

Automatically detects which AI API (Ollama, OpenAI, etc.) a URL supports
and returns the appropriate classifier.
"""
import httpx
from typing import Type, Union
from pathlib import Path

from ..utils import log
from .base import Classifier
from .ollama import OllamaClassifier
from .openai import OpenAIClassifier


# Type alias for concrete classifier classes
ClassifierClass = Type[Union[OllamaClassifier, OpenAIClassifier]]


def load_prompt(path: str | Path) -> str:
    """Load prompt from file.
    
    Args:
        path: Path to prompt file
        
    Returns:
        Loaded prompt string
        
    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_path = Path(path)
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text().strip()


# Cache for endpoint detection to avoid repeated probing
_endpoint_type_cache: dict[str, ClassifierClass | None] = {}


def create_ai_classifier(
    url: str,
    api_key: str | None = None,
    model: str | None = None,
    max_concurrency: int | None = None,
    file_prompt_template: str | None = None,
    folder_prompt_template: str | None = None,
    **kwargs,
) -> Classifier:
    """Create appropriate AI classifier by auto-detecting endpoint type.
    
    Strategy:
    1. Check cache for previously detected endpoint
    2. Try fast URL pattern detection
    3. Probe endpoint to determine API type
    4. Instantiate and verify availability
    
    Args:
        url: Base URL of AI endpoint
        api_key: Optional API key for OpenAI-compatible endpoints
        model: Model name (auto-detected if not provided)
        max_concurrency: Max concurrent requests
        file_prompt_template: Custom file classification prompt
        folder_prompt_template: Custom folder action prompt
        **kwargs: Additional classifier-specific arguments
        
    Returns:
        Configured classifier instance
        
    Raises:
        ValueError: If no compatible classifier found
    """
    normalized_url = url.rstrip("/")
    
    # Check cache
    if normalized_url in _endpoint_type_cache:
        cached_cls = _endpoint_type_cache[normalized_url]
        if cached_cls:
            log.debug(f"ai_auto: using cached classifier type for {normalized_url}: {cached_cls.__name__}")
            return _instantiate_classifier(
                cached_cls, normalized_url, api_key, model,
                max_concurrency, file_prompt_template, folder_prompt_template, kwargs
            )
    
    # Try fast detection from URL patterns
    detected_cls = _detect_from_url_pattern(normalized_url)
    if detected_cls:
        log.debug(f"ai_auto: detected from URL pattern: {detected_cls.__name__}")
        classifier = _instantiate_classifier(
            detected_cls, normalized_url, api_key, model,
            max_concurrency, file_prompt_template, folder_prompt_template, kwargs
        )
        if classifier.ensure_available():
            _endpoint_type_cache[normalized_url] = detected_cls
            return classifier
        log.warning(f"ai_auto: pattern detection found {detected_cls.__name__} but endpoint not available")
    
    # Probe endpoint - try multiple APIs in order
    log.debug(f"ai_auto: probing endpoint {normalized_url}")
    classifier_classes: list[ClassifierClass] = [OllamaClassifier, OpenAIClassifier]
    for attempt_cls in classifier_classes:
        try:
            log.debug(f"ai_auto: trying {attempt_cls.__name__}")
            classifier = _instantiate_classifier(
                attempt_cls, normalized_url, api_key, model,
                max_concurrency, file_prompt_template, folder_prompt_template, kwargs
            )
            if classifier.ensure_available():
                log.info(f"ai_auto: detected working {attempt_cls.__name__} at {normalized_url}")
                _endpoint_type_cache[normalized_url] = attempt_cls
                return classifier
            else:
                log.debug(f"ai_auto: {attempt_cls.__name__} not available at {normalized_url}")
        except Exception as e:
            log.debug(f"ai_auto: {attempt_cls.__name__} failed: {e}")
    
    # Nothing worked
    _endpoint_type_cache[normalized_url] = None
    raise ValueError(f"Could not detect compatible AI API at {normalized_url}")


def _detect_from_url_pattern(url: str) -> ClassifierClass | None:
    """Fast detection based on URL patterns."""
    url_lower = url.lower()
    
    # OpenAI patterns
    if any(pattern in url_lower for pattern in ["openai.com", "azure", "/v1/", "api.groq"]):
        return OpenAIClassifier
    
    # Ollama patterns
    if any(pattern in url_lower for pattern in ["ollama", ":11434", "/api/tags", "/api/chat"]):
        return OllamaClassifier
    
    return None


def _probe_endpoint(url: str, api_key: str | None = None) -> ClassifierClass | None:
    """Probe endpoint to determine API type.
    
    Tries to call common endpoints to detect API type:
    - /v1/models (OpenAI)
    - /api/tags (Ollama)
    - /api/version (Ollama)
    """
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    with httpx.Client(timeout=5.0, headers=headers) as client:
        # Try OpenAI /v1/models
        try:
            resp = client.get(f"{url}/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                # Check for error field (LM Studio returns 200 with error for unsupported endpoints)
                if "error" in data:
                    log.debug(f"ai_auto: {url}/v1/models returned error: {data.get('error')}")
                elif "data" in data or "object" in data:
                    log.debug(f"ai_auto: probe found OpenAI-compatible API at {url}/v1/models")
                    return OpenAIClassifier
        except Exception:
            pass
        
        # Try Ollama /api/tags
        try:
            resp = client.get(f"{url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                # Check for error field
                if "error" in data:
                    log.debug(f"ai_auto: {url}/api/tags returned error: {data.get('error')}")
                elif "models" in data or isinstance(data, list):
                    log.debug(f"ai_auto: probe found Ollama API at {url}/api/tags")
                    return OllamaClassifier
        except Exception:
            pass
        
        # Try Ollama /api/version
        try:
            resp = client.get(f"{url}/api/version")
            if resp.status_code == 200:
                log.debug(f"ai_auto: probe found Ollama API at {url}/api/version")
                return OllamaClassifier
        except Exception:
            pass
    
    return None


def _instantiate_classifier(
    cls: ClassifierClass,
    url: str,
    api_key: str | None,
    model: str | None,
    max_concurrency: int | None,
    file_prompt_template: str | None,
    folder_prompt_template: str | None,
    extra_kwargs: dict,
) -> Classifier:
    """Instantiate classifier with appropriate arguments."""
    
    # Type narrowing: check which concrete class we have
    if cls is OllamaClassifier:
        if not model:
            raise ValueError("OllamaClassifier requires a model name")
        return OllamaClassifier(
            url=url,
            model=model,
            max_concurrency=max_concurrency,
            prompt_template=file_prompt_template,
            folder_prompt_template=folder_prompt_template,
            **extra_kwargs,
        )
    
    if cls is OpenAIClassifier:
        # Auto-detect model if not provided
        if not model:
            url_lower = url.lower()
            model = "gpt-35-turbo" if "azure" in url_lower else "gpt-3.5-turbo"
        
        return OpenAIClassifier(
            url=url,
            model=model,
            api_key=api_key,
            max_concurrency=max_concurrency,
            file_prompt_template=file_prompt_template,
            folder_prompt_template=folder_prompt_template,
            **extra_kwargs,
        )
    
    raise ValueError(f"Unknown classifier type: {cls}")


def clear_cache():
    """Clear endpoint detection cache. Useful for testing."""
    _endpoint_type_cache.clear()
