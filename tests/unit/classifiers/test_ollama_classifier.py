import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.categories import CategoryPath
from app.classifiers import OllamaClassifier


def test_classify_uses_metric(monkeypatch):
    async def run():
        mock_client = MagicMock()
        response = MagicMock()
        response.json.return_value = {"message": {"content": "Thought: path indicates photos\nAnswer: Media/Photos"}}
        response.raise_for_status.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        classifier = OllamaClassifier(url="http://example.com", model="test-model", max_concurrency=1)
        monkeypatch.setattr(classifier, "_get_client", lambda: ("http://example.com", mock_client))

        hint = {"source_path": "/full/path/pic.jpg", "rule_category_path": "Media/Photos"}
        result = await classifier.classify("pic.jpg", "photos/pic.jpg", "image/jpeg", "", hint=hint)

        assert result.path == CategoryPath("Media/Photos")
        assert isinstance(result.metrics, dict)
        assert result.metrics["raw_output"] == "Thought: path indicates photos\nAnswer: Media/Photos"
        assert "Thought: path indicates photos" in result.metrics["reasoning"]
        assert result.metrics["raw_response"]["message"]["content"].endswith("Media/Photos")
        assert mock_client.post.await_count == 1
        sent = mock_client.post.await_args.kwargs["json"]["messages"][1]["content"]
        assert "Rule Hint: Media/Photos" in sent
        assert "Path: /full/path/pic.jpg" in sent

    asyncio.run(run())


def test_classify_handles_failure(monkeypatch):
    async def run():
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("boom"))
        classifier = OllamaClassifier(url="http://example.com", model="test-model", max_concurrency=1)
        monkeypatch.setattr(classifier, "_get_client", lambda: ("http://example.com", mock_client))
        classifier.sem._value = 1

        result = await classifier.classify("pic.jpg", "photos/pic.jpg", "image/jpeg", "")

        assert result.path == CategoryPath("Unknown")
        assert isinstance(result.metrics, dict)
        assert mock_client.post.await_count >= 1

    asyncio.run(run())


def test_parser_fallback_to_content(monkeypatch):
    async def run():
        mock_client = MagicMock()
        response = MagicMock()
        response.json.return_value = {"message": {"content": "Media/Photos"}}
        response.raise_for_status.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        classifier = OllamaClassifier(url="http://example.com", model="test-model", max_concurrency=1)
        monkeypatch.setattr(classifier, "_get_client", lambda: ("http://example.com", mock_client))
        result = await classifier.classify("pic.jpg", "photos/pic.jpg", "image/jpeg", "")
        assert result.path == CategoryPath("Media/Photos")
        assert "reasoning" not in result.metrics

    asyncio.run(run())


def test_custom_prompt_template_inserts_catalog(monkeypatch):
    async def run():
        mock_client = MagicMock()
        response = MagicMock()
        response.json.return_value = {"message": {"content": "Media/Photos"}}
        response.raise_for_status.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        template = "Select a path from {categories_json}. Never trust hints."
        classifier = OllamaClassifier(url="http://example.com", model="test-model", max_concurrency=1, prompt_template=template)
        monkeypatch.setattr(classifier, "_get_client", lambda: ("http://example.com", mock_client))
        await classifier.classify("pic.jpg", "photos/pic.jpg", "image/jpeg", "")
        sys_prompt = mock_client.post.await_args.kwargs["json"]["messages"][0]["content"]
        assert "{categories_json}" not in sys_prompt
        assert "Never trust hints." in sys_prompt
        assert "Media" in sys_prompt

    asyncio.run(run())


def test_prompt_template_without_placeholder_appends_catalog(monkeypatch):
    async def run():
        mock_client = MagicMock()
        response = MagicMock()
        response.json.return_value = {"message": {"content": "Media/Photos"}}
        response.raise_for_status.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        template = "Judge carefully."
        classifier = OllamaClassifier(url="http://example.com", model="test-model", max_concurrency=1, prompt_template=template)
        monkeypatch.setattr(classifier, "_get_client", lambda: ("http://example.com", mock_client))
        await classifier.classify("pic.jpg", "photos/pic.jpg", "image/jpeg", "")
        sys_prompt = mock_client.post.await_args.kwargs["json"]["messages"][0]["content"]
        assert sys_prompt.startswith("Judge carefully.")
        assert "Categories JSON:" in sys_prompt

    asyncio.run(run())


def test_classify_preserves_custom_suffix(monkeypatch):
    async def run():
        mock_client = MagicMock()
        response = MagicMock()
        response.json.return_value = {"message": {"content": "Thought: academic notes\nAnswer: Documents/Other/Academic"}}
        response.raise_for_status.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        classifier = OllamaClassifier(url="http://example.com", model="test-model", max_concurrency=1)
        monkeypatch.setattr(classifier, "_get_client", lambda: ("http://example.com", mock_client))

        result = await classifier.classify("notes.pdf", "notes.pdf", "application/pdf", "")
        assert result.path == CategoryPath("Documents/Other/Academic")

    asyncio.run(run())
