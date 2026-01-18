import sys
from pathlib import Path

import asyncio
import pytest

# Ensure app.* importable
THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.categories import CategoryPath
from app.classifiers import (
    Classifier,
    ClassifierResponse,
    MultiplexedClassifier,
    OllamaClassifier,
    RulesClassifier,
)


REQUIRED_METHODS = [
    "classify",
    "close",
    "advise_folder_action",
    "ensure_available",
    "display_name",
    "is_ai",
]


def _has_interface(obj):
    for name in REQUIRED_METHODS:
        attr = getattr(obj, name, None)
        assert callable(attr), f"{obj.__class__.__name__} missing callable {name}"


def test_rules_classifier_interface(tmp_path):
    rules_path = tmp_path / "rules.csv"
    rules_path.write_text("^.*$,.*,Unknown,,\n", encoding="utf-8")
    clf = RulesClassifier(rules_path)
    _has_interface(clf)


def test_ollama_classifier_interface():
    clf = OllamaClassifier(url="http://example.com", model="test-model", max_concurrency=1)
    _has_interface(clf)
    asyncio.run(clf.close())


class _MockWorker:
    async def classify(self, *args, **kwargs):
        return ClassifierResponse(CategoryPath("Unknown"), {"source": "mock"})

    async def close(self):
        return None

    def advise_folder_action(self, request):
        from app.classifiers.base import FolderActionResponse
        from app.folder_action import FolderAction
        return FolderActionResponse.decision(FolderAction.DISAGGREGATE, reason="mock")

    def ensure_available(self):
        return True

    def display_name(self) -> str:
        return "mock"

    def is_ai(self) -> bool:
        return True


def test_multiplexed_classifier_interface():
    clf = MultiplexedClassifier([_MockWorker()])
    _has_interface(clf)
    asyncio.run(clf.close())
