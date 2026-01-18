import os

import pytest

from app.config import AppConfig


def test_ollama_endpoints_requires_url(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("CLASSIFIER", raising=False)
    with pytest.raises(ValueError):
        AppConfig.from_env()


def test_ollama_endpoints_custom(monkeypatch):
    monkeypatch.delenv("CLASSIFIER", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://a:1|2|model-a,http://b:2|5|model-b,http://c:3|4|model-c")
    monkeypatch.setenv("OLLAMA_WORKERS", "4")
    cfg = AppConfig.from_env()
    eps = cfg.ollama_endpoints()
    assert eps == [
        ("http://a:1", 2, "model-a"),
        ("http://b:2", 5, "model-b"),
        ("http://c:3", 4, "model-c"),
    ]


def test_manual_classifier_allows_missing_ollama(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.setenv("CLASSIFIER", "manual")
    cfg = AppConfig.from_env()
    assert cfg.CLASSIFIER_KIND == "manual"
    assert cfg.ollama_endpoints() == []
