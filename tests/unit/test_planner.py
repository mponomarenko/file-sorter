import sys
import asyncio
import tempfile
from pathlib import Path
from dataclasses import replace

# Ensure we can import `app.*`
THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.categories import CategoryPath
from app.config import AppConfig
from app.media import MediaHelper
from app.planner import Planner
from app.classifiers import ClassifierResponse
from app.classification_records import ClassificationRecord


class FakeDB:
    """Minimal in-memory stand-in that mimics Database.select_unclassified/update_category_dest."""

    def __init__(self, rows):
        # Support both 2-tuple (path, mime) and 3-tuple (path, mime, size)
        self._rows = []
        for row in rows:
            if len(row) == 2:
                path, mime = row
                size = 0  # Default size
            else:
                path, mime, size = row
            self._rows.append({"path": path, "mime": mime, "size": size})
        self.updates: list[ClassificationRecord] = []

    def select_unclassified(self, limit=None):
        rows = [(row["path"], row["mime"], row["size"]) for row in self._rows]
        if limit is not None:
            return rows[:limit]
        return rows

    def update_category_dest(self, updates):
        for entry in updates:
            if not isinstance(entry, ClassificationRecord):
                raise TypeError("FakeDB expects ClassificationRecord payloads")
            self.updates.append(entry)
        self._rows.clear()
    
    def count_all_files(self):
        """Return the number of files in the database."""
        return len(self._rows)
    
    def get_folder_actions(self):
        """Return empty dict for folder actions (test stub)."""
        return {}
    
    def save_folder_actions(self, actions, decisions):
        """No-op for tests."""
        pass
    
    def connect(self):
        """Mock connect for planner._get_persisted_decisions."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE folder_actions (folder_path TEXT, decision_source TEXT)")
        return conn

class FakeOllamaClassifier:
    def __init__(
        self,
        url: str,
        max_concurrency: int | None = None,
        decisions: dict[str, object] | None = None,
        folder_advices: dict[str, str] | None = None,
    ):
        self.calls = []
        self.url = url
        self.decisions = {self._normalize_key(key): self._normalize_decision(value) for key, value in (decisions or {}).items()}
        self.folder_advices = {self._normalize_key(key): value for key, value in (folder_advices or {}).items()}

    @staticmethod
    def _normalize_key(value: str | None) -> str:
        if not value:
            return ""
        text = value.replace("\\", "/").strip()
        while text.startswith("./"):
            text = text[2:]
        return text.lstrip("/")

    @staticmethod
    def _normalize_decision(value: object) -> dict[str, object]:
        if value is None:
            return {}
        if isinstance(value, CategoryPath):
            return {"category": value.label, "metadata": {}}
        if isinstance(value, str):
            return {"category": value, "metadata": {}}
        if isinstance(value, (list, tuple)):
            items = list(value)
            if not items:
                return {}
            category = items[0]
            metadata = items[1] if len(items) > 1 else {}
            return {
                "category": category,
                "metadata": dict(metadata) if isinstance(metadata, dict) else {},
            }
        if isinstance(value, dict):
            category = value.get("category") or value.get("path") or value.get("label") or "Unknown"
            metadata = value.get("metadata") or {}
            return {"category": category, "metadata": dict(metadata) if isinstance(metadata, dict) else {}}
        return {"category": str(value), "metadata": {}}

    def _lookup_decision(self, name: str, rel: str, hint: dict | None) -> dict[str, object] | None:
        candidates = [self._normalize_key(rel), self._normalize_key(name)]
        if hint:
            src = hint.get("source_path")
            candidates.append(self._normalize_key(src if isinstance(src, str) else None))
        for key in candidates:
            if key and key in self.decisions:
                return self.decisions[key]
        return None

    async def classify(self, name, rel, mime, sample, hint=None):
        self.calls.append((name, rel, mime, sample))
        decision = self._lookup_decision(name, rel, hint)
        if not decision:
            return ClassifierResponse(CategoryPath("Unknown"), {"source": "fake", "matched": False})

        category = decision.get("category") or "Unknown"
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        metrics = {"source": "fake", "matched": True}
        if metadata:
            metrics["metadata"] = metadata
        return ClassifierResponse(CategoryPath(category), metrics)

    async def close(self):
        return None

    def ensure_available(self) -> bool:
        return True

    def advise_folder_action(self, request):
        from app.classifiers.base import FolderActionResponse
        from app.folder_action import FolderAction
        
        # Check if folder is in our advice map
        key = self._normalize_key(request.folder_path)
        if key and key in self.folder_advices:
            advice = self.folder_advices[key]
            try:
                action = FolderAction.from_string(advice)
                return FolderActionResponse.decision(action, reason="test:configured")
            except ValueError:
                pass
        
        # Default
        return FolderActionResponse.decision(FolderAction.DISAGGREGATE, reason="test:default")

    def display_name(self) -> str:
        return "fake-ollama"

    def is_ai(self) -> bool:
        return True


def test_classify_and_plan_no_llm():
    with tempfile.TemporaryDirectory() as td:
        a = Path(td, "a.jpg"); a.write_bytes(b"x")
        b = Path(td, "b.mp4"); b.write_bytes(b"x")
        c = Path(td, "c.mp3"); c.write_bytes(b"x")
        fdb = FakeDB([
            (str(a), "image/jpeg"),
            (str(b), "video/mp4"),
            (str(c), "audio/mpeg"),
        ])
        cfg = replace(AppConfig.from_env(), MAIN_TARGET="/target", SOURCES=[str(td)], CLASSIFIER_KIND="manual")
        media = MediaHelper(cfg)
        planner = Planner(cfg, fdb, media)

        asyncio.run(planner.classify_and_plan())
        assert fdb.updates and len(fdb.updates) == 3
        cats = {entry.path: entry.category_label for entry in fdb.updates}
        assert cats[str(a)] == "Media/Photos"
        assert cats[str(b)] == "Media/Videos/Movies"
        assert cats[str(c)] == "Media/Music"


def test_classify_and_plan_with_llm_and_peek():
    with tempfile.TemporaryDirectory() as td:
        x = Path(td, "x.bin"); x.write_bytes(b"x")
        fdb = FakeDB([(str(x), "application/octet-stream")])
        cfg = replace(AppConfig.from_env(), MAIN_TARGET="/target", SOURCES=[str(td)])
        media = MediaHelper(cfg)
        planner = Planner(cfg, fdb, media)

        planner.classifier_factory = lambda url, model=None, max_concurrency=None: FakeOllamaClassifier(url, max_concurrency)
        planner.peek_text_fn = lambda path, mime, n: "sample text"

        asyncio.run(planner.classify_and_plan())
        assert fdb.updates and len(fdb.updates) == 1
        record = fdb.updates[0]
        assert record.category_label == "Unknown"


def test_classify_rule_only_skips_llm(tmp_path):
    src = Path(tmp_path, "proj")
    src.mkdir()
    code = src / "main.py"
    code.write_text("print('hi')")

    fdb = FakeDB([(str(code), "text/x-python")])
    cfg = replace(AppConfig.from_env(), MAIN_TARGET="/target", SOURCES=[str(tmp_path)], CLASSIFIER_KIND="manual")
    media = MediaHelper(cfg)
    planner = Planner(cfg, fdb, media)

    fake = FakeOllamaClassifier("http://fake")
    planner.classifier_factory = lambda url, model=None, max_concurrency=None: fake

    asyncio.run(planner.classify_and_plan())

    assert fake.calls == []
    assert fdb.updates and len(fdb.updates) == 1
    entry = fdb.updates[0]
    assert entry.category_label == "Software/Source_Code"


def test_ai_classifier_applies_custom_category_and_metadata(tmp_path):
    doc = Path(tmp_path, "Downloads/Docs/resume.docx")
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"payload")

    fdb = FakeDB([(str(doc), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
    cfg = replace(AppConfig.from_env(), MAIN_TARGET="/target", SOURCES=[str(tmp_path)])
    media = MediaHelper(cfg)
    planner = Planner(cfg, fdb, media)

    decisions = {
        "Downloads/Docs/resume.docx": {
            "category": "Documents/Other",
            "metadata": {"ai_category": "Resumes/Engineering"},
        }
    }
    fake = FakeOllamaClassifier("http://fake", decisions=decisions)
    planner.classifier_factory = lambda url, model=None, max_concurrency=None: fake
    planner.peek_text_fn = lambda path, mime, n: "sample text"

    asyncio.run(planner.classify_and_plan())

    assert fake.calls, "Expected AI classifier to be invoked"
    assert fdb.updates and len(fdb.updates) == 1
    record = fdb.updates[0]
    assert record.category_label == "Documents/Other"
    # Year should be included in path now (from file modification time)
    assert "/Documents/Other/" in record.destination
    assert "/Resumes/Engineering/resume.docx" in record.destination
    metadata = record.parsed_metadata()
    assert metadata["ai_category"] == "Resumes/Engineering"


def test_ai_classifier_guides_unknown_projects_folder(tmp_path):
    proj = Path(tmp_path, "Downloads/Projects/app/design.proj")
    proj.parent.mkdir(parents=True)
    proj.write_text("draft")

    fdb = FakeDB([(str(proj), "application/octet-stream")])
    cfg = replace(AppConfig.from_env(), MAIN_TARGET="/target", SOURCES=[str(tmp_path)])
    media = MediaHelper(cfg)
    planner = Planner(cfg, fdb, media)

    decisions = {
        "Downloads/Projects/app/design.proj": "Software/Source_Code/Projects/app",
    }
    fake = FakeOllamaClassifier("http://fake", decisions=decisions)
    planner.classifier_factory = lambda url, model=None, max_concurrency=None: fake
    planner.peek_text_fn = lambda path, mime, n: ""

    asyncio.run(planner.classify_and_plan())

    assert fake.calls and len(fake.calls) == 1
    assert fdb.updates and len(fdb.updates) == 1
    record = fdb.updates[0]
    assert record.category_label == "Software/Source_Code/Projects/app"
    assert record.destination == "/target/Software/Source_Code/Projects/app/design.proj"
