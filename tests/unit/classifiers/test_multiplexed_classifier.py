import asyncio
from typing import List
import time
import sys
from pathlib import Path

# Add the project root to sys.path for imports
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.categories import CategoryPath
from app.classifiers import MultiplexedClassifier, Classifier, ClassifierResponse
from app.classifiers.base import FolderActionResponse
from app.folder_action import FolderAction, FolderActionRequest

class MockClassifier:
    def __init__(self, delay: float = 0.1, fail_rate: float = 0.0):
        self.delay = delay
        self.fail_rate = fail_rate
        self.calls = []
        self.folder_calls = []
        self.closed = False

    async def classify(self, name: str, rel_path: str, mime: str, sample: str, hint=None) -> ClassifierResponse:
        self.calls.append((name, rel_path, mime, sample))
        time.sleep(self.delay)
        if self.fail_rate > 0:
            raise Exception("Simulated failure")
        return ClassifierResponse(CategoryPath("Test"), {"source": "mock"})

    def advise_folder_action(self, request: FolderActionRequest) -> FolderActionResponse:
        self.folder_calls.append(request)
        time.sleep(self.delay)
        if self.fail_rate > 0:
            raise Exception("Simulated failure")
        # For testing, assume folders with more than 10 files should stay as unit
        action = FolderAction.KEEP if request.total_files > 10 else FolderAction.DISAGGREGATE
        return FolderActionResponse.decision(action, reason="mock:test")

    def ensure_available(self) -> bool:
        return self.fail_rate < 1.0

    async def close(self):
        self.closed = True

    def display_name(self) -> str:
        return f"mock({self.delay})"

    def is_ai(self) -> bool:
        return True

def test_basic_classification():
    async def run():
        c1 = MockClassifier(delay=0.1)
        c2 = MockClassifier(delay=0.2)

        classifier = MultiplexedClassifier([c1, c2])
        results = [
            await classifier.classify(f"file{i}.txt", "rel/path", "text/plain", "sample")
            for i in range(5)
        ]

        assert len(results) == 5
        assert len(c1.calls) > len(c2.calls)

    asyncio.run(run())


def test_failure_handling():
    async def run():
        c1 = MockClassifier(delay=0.01, fail_rate=1.0)

        classifier = MultiplexedClassifier([c1])
        assert classifier.ensure_available() is False

        c2 = MockClassifier(delay=0.01)
        c3 = MockClassifier(delay=0.02)
        classifier = MultiplexedClassifier([c2, c3])

        await classifier.classify("file.txt", "rel/path", "text/plain", "sample")
        assert len(c2.calls) == 1
        assert len(c3.calls) == 0

    asyncio.run(run())


def test_stats_dumping():
    async def run():
        c1 = MockClassifier(delay=0.1)

        classifier = MultiplexedClassifier([c1], stats_interval=1)
        for i in range(5):
            await classifier.classify(f"file{i}.txt", "rel/path", "text/plain", "sample")

        time.sleep(1.1)
        snapshot = classifier.workers[0].total.snapshot()
        assert snapshot.requests == 5
        assert snapshot.success == 5
        assert snapshot.failure == 0
        assert snapshot.latency_ms > 0

    asyncio.run(run())

def test_folder_action_routing():
    # Create classifiers with different delays
    c1 = MockClassifier(delay=0.1)
    c2 = MockClassifier(delay=0.2)
    
    classifier = MultiplexedClassifier([c1, c2])
    
    # Test folder decisions
    request = FolderActionRequest(
        folder_path="/test",
        folder_name="test",
        children=[],
        total_files=2,
        rule_hint=None,
    )
    
    # First request should go to c1 (faster)
    result = classifier.advise_folder_action(request)
    assert result.action == FolderAction.DISAGGREGATE  # Based on mock's logic for total_files <= 10
    assert result.is_final
    assert len(c1.folder_calls) == 1
    assert len(c2.folder_calls) == 0
    
    # Larger folders should keep being assigned, and the slower worker should receive some traffic
    request_large = FolderActionRequest(
        folder_path="/test",
        folder_name="test",
        children=[],
        total_files=20,
        rule_hint=None,
    )
    for _ in range(3):
        result = classifier.advise_folder_action(request_large)
        assert result.action == FolderAction.KEEP
        assert result.is_final

    # Faster worker handles more requests but the slower worker should not remain idle
    assert len(c1.folder_calls) >= 1
    assert len(c2.folder_calls) >= 1

def test_close_propagates_to_workers():
    async def run():
        c1 = MockClassifier(delay=0.01)
        c2 = MockClassifier(delay=0.02)
        classifier = MultiplexedClassifier([c1, c2])

        await classifier.close()

        assert c1.closed is True
        assert c2.closed is True

    asyncio.run(run())
