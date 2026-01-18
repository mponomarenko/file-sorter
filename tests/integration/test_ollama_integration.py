import asyncio
import importlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest

# Ensure we can import `app.*` by adding `file-sorter` to sys.path
THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent
ROOT = TOP.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig, config as app_config
from app.orchestrator import Orchestrator


def _ollama_url_and_model() -> tuple[str, str]:
    endpoints = app_config.ollama_endpoints()
    if endpoints:
        return endpoints[0][0], endpoints[0][2]
    return "http://localhost:11434", "test-model"


def _ollama_ready() -> bool:
    import app.classifiers.ollama as oc

    importlib.reload(oc)
    url, model = _ollama_url_and_model()
    instance = oc.OllamaClassifier(url=(url := _ollama_url_and_model()[0]), model=_ollama_url_and_model()[1], model=model="test-model")
    try:
        return instance.ensure_available()
    finally:
        asyncio.get_event_loop().run_until_complete(instance.close())


def test_prompts_directory_exists():
    """Verify that prompt templates are available in the container."""
    prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"
    assert prompts_dir.exists(), f"Prompts directory not found: {prompts_dir}"
    
    file_prompt = prompts_dir / "file_classification_default.prompt"
    assert file_prompt.exists(), f"Default file prompt not found: {file_prompt}"
    
    folder_prompt = prompts_dir / "folder_action_default.prompt"
    assert folder_prompt.exists(), f"Default folder prompt not found: {folder_prompt}"


def test_ollama_classifier_initialization():
    """Test that OllamaClassifier can be initialized with default prompts."""
    import app.classifiers.ollama as oc

    importlib.reload(oc)
    url, model = _ollama_url_and_model()
    # This should not raise FileNotFoundError for missing prompts
    instance = oc.OllamaClassifier(url=url, model=model)
    try:
        # Verify the prompt templates were loaded
        assert instance.prompt_template, "File prompt template should be loaded"
        assert instance.folder_prompt_template, "Folder prompt template should be loaded"
    finally:
        asyncio.get_event_loop().run_until_complete(instance.close())


def test_ollama_availability():
    import app.classifiers.ollama as oc

    importlib.reload(oc)
    url, model = _ollama_url_and_model()
    instance = oc.OllamaClassifier(url=(url := _ollama_url_and_model()[0]), model=_ollama_url_and_model()[1], model=model="test-model")
    try:
        ok = instance.ensure_available()
        if not ok:
            pytest.skip("Ollama not available in this environment; skipping")
    finally:
        asyncio.get_event_loop().run_until_complete(instance.close())


def test_classify_document_when_available():
    import app.classifiers.ollama as oc
    from app.path_models import CategoryPath

    importlib.reload(oc)
    url, model = _ollama_url_and_model()
    pool = oc.OllamaClassifier(url=(url := _ollama_url_and_model()[0]), model=_ollama_url_and_model()[1], model=, model="test-model", max_concurrency=1)
    try:
        response = asyncio.get_event_loop().run_until_complete(
            pool.classify("note.txt", "docs/note.txt", "text/plain", "hello world this is a document")
        )
        normalized = oc.CATEGORIES.normalize(response.path)
        assert normalized is not None, f"Category {response.path} not found in categories"
    finally:
        asyncio.get_event_loop().run_until_complete(pool.close())


def test_folder_advisory_when_available():
    import app.classifiers.ollama as oc

    importlib.reload(oc)
    payload = {
        "folders": ["/fixtures/paths/system/Downloads"],
        "summaries": [
            {
                "folder": "/fixtures/paths/system/Downloads",
                "file_count_sampled": 3,
                "approx_bytes": 1234,
                "top_exts": [[".pdf", 1], [".jpg", 1], [".exe", 1]],
                "samples": [
                    {"rel": "scan1.pdf", "mime": "application/pdf"},
                    {"rel": "receipt.jpg", "mime": "image/jpeg"},
                    {"rel": "setup.exe", "mime": "application/x-msdownload"},
                ],
            }
        ],
        "hints_safe_to_drop": ["Downloads"],
    }
    url, model = _ollama_url_and_model()
    classifier = oc.OllamaClassifier(url=(url := _ollama_url_and_model()[0]), model=_ollama_url_and_model()[1], model=, model="test-model", max_concurrency=1)
    try:
        decision = classifier.advise_folder_action(payload)
    finally:
        asyncio.get_event_loop().run_until_complete(classifier.close())
    assert decision in ("move_as_unit", "disaggregate", "skip")


def test_end_to_end_duplicate_folders_without_llm(tmp_path):
    """Exercise scanning + folder hashing + duplicate detection without LLM."""
    src1 = tmp_path / "src1" / "Proj"
    src2 = tmp_path / "src2" / "Proj"
    for root in (src1, src2):
        root.mkdir(parents=True)
        (root / "file.txt").write_text("same")

    base_cfg = AppConfig.from_env()
    cfg = replace(
        base_cfg,
        SOURCES=[str(tmp_path / "src1"), str(tmp_path / "src2")],
        MAIN_TARGET=str(tmp_path / "target"),
        REPORT_DIR=str(tmp_path / "reports"),
        DB_PATH=str(tmp_path / "catalog.sqlite"),
        CLASSIFIER_KIND="manual",
    )
    orch = Orchestrator(cfg)

    orch.scan_paths()
    orch.write_report()

    dup_reports = sorted(Path(cfg.REPORT_DIR).glob("duplicate_folders_*.csv"))
    assert dup_reports, "Expected duplicate folders report to be generated"
    dtxt = dup_reports[-1].read_text(encoding="utf-8", errors="ignore")
    assert str(src1) in dtxt and str(src2) in dtxt


def test_end_to_end_with_llm_and_reports(tmp_path):
    """Run scan + classify with Ollama if available, then write reports."""
    if not _ollama_ready():
        pytest.skip("Ollama/model not available for integration run")

    source_root = tmp_path / "src"
    docs = source_root / "Docs"
    docs.mkdir(parents=True)
    doc_path = docs / "note.txt"
    doc_path.write_text("integration test document")

    base_cfg = AppConfig.from_env()
    cfg = replace(
        base_cfg,
        SOURCES=[str(source_root)],
        MAIN_TARGET=str(tmp_path / "target"),
        REPORT_DIR=str(tmp_path / "reports"),
        DB_PATH=str(tmp_path / "catalog_llm.sqlite"),
        CLASSIFIER_KIND="ollama",
    )
    orch = Orchestrator(cfg)
    classifier = orch._choose_classifier()
    if not classifier or not classifier.ensure_available():
        pytest.skip("Classifier not available for LLM integration test")

    orch.scan_paths()
    asyncio.get_event_loop().run_until_complete(orch._classify_and_plan(classifier))
    orch.write_report()

    cleanup_reports = sorted(Path(cfg.REPORT_DIR).glob("cleanup_report_*.csv"))
    assert cleanup_reports, "Expected cleanup report to exist"
    report_text = cleanup_reports[-1].read_text(encoding="utf-8", errors="ignore")
    assert str(doc_path) in report_text
