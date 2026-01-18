import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from app.categories import CategoryPath
from app.classification_records import ClassificationRecordBuilder
from app.config import AppConfig
from app.db import Database
from app.file_metadata import FileMetadata
from app.file_nodes import FileNodeBuilder
from app.media import MediaHelper


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = REPO_ROOT / "cli" / "analyze_full.py"


def _build_cfg(db_path: Path, source_dir: Path, target_dir: Path, report_dir: Path) -> AppConfig:
    base_cfg = AppConfig.from_env()
    return replace(
        base_cfg,
        DB_PATH=str(db_path),
        SOURCES=[str(source_dir)],
        MAIN_TARGET=str(target_dir),
        REPORT_DIR=str(report_dir),
        CLASSIFIER_KIND="manual",
    )


def _env_for_cli(db_path: Path, source_dir: Path, target_dir: Path, report_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DB_PATH": str(db_path),
            "SOURCES": str(source_dir),
            "MAIN_TARGET": str(target_dir),
            "REPORT_DIR": str(report_dir),
            "CLASSIFIER": "manual",
            "STRIP_DIRS": "",
        }
    )
    return env


def _run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result


def _run_cli_expect_failure(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "Expected CLI to fail but it succeeded"
    return result


def test_cli_analyze_save_persists_classification(tmp_path):
    source_dir = tmp_path / "src"
    target_dir = tmp_path / "target"
    report_dir = tmp_path / "reports"
    db_path = tmp_path / "catalog.sqlite"
    source_dir.mkdir()
    target_dir.mkdir()
    report_dir.mkdir()

    sample = source_dir / "main.py"
    sample.write_text("print('hi')")

    cfg = _build_cfg(db_path, source_dir, target_dir, report_dir)
    database = Database(cfg)
    stat = sample.stat()
    database.bulk_insert(
        [
            (
                str(sample),
                stat.st_size,
                stat.st_mtime,
                "text/x-python",
                "hash-main",
                "scanned",
            )
        ]
    )

    env = _env_for_cli(db_path, source_dir, target_dir, report_dir)
    _run_cli(
        [
            str(sample),
            "--no-ai",
            "--save",
        ],
        env,
    )

    rows = list(Database(cfg).iter_all())
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == str(sample)
    assert row[4] == "Software/Source_Code"
    assert row[5] and row[5].endswith("main.py")
    assert row[8] is not None


def test_cli_analyze_db_mode_outputs_saved_rows(tmp_path):
    source_dir = tmp_path / "src"
    target_dir = tmp_path / "target"
    report_dir = tmp_path / "reports"
    db_path = tmp_path / "catalog.sqlite"
    source_dir.mkdir()
    target_dir.mkdir()
    report_dir.mkdir()

    track = source_dir / "song.mp3"
    track.write_bytes(b"\x00\x01demo")

    cfg = _build_cfg(db_path, source_dir, target_dir, report_dir)
    media = MediaHelper(cfg)
    database = Database(cfg)
    stat = track.stat()
    database.bulk_insert(
        [
            (
                str(track),
                stat.st_size,
                stat.st_mtime,
                "audio/mpeg",
                "hash-track",
                "scanned",
            )
        ]
    )

    node_builder = FileNodeBuilder(
        sources=[str(source_dir)],
        source_wrapper_pattern=cfg.SOURCE_WRAPPER_REGEX,
    )
    metadata = FileMetadata()
    metadata.add("artist", "Unit Test")
    metadata.add("album", "CLI Coverage")
    node = node_builder.build(
        str(track),
        category=CategoryPath("Media", "Music"),
        mime="audio/mpeg",
        metadata=metadata,
        rule_match=None,
    )
    destination = media.build_destination(node)
    record_builder = ClassificationRecordBuilder(cfg)
    database.update_category_dest([record_builder.build(node, destination)])

    env = _env_for_cli(db_path, source_dir, target_dir, report_dir)
    result = _run_cli(
        [
            "--mode",
            "db",
            "--output-json",
            "--db-limit",
            "5",
            "--path-filter",
            "song.mp3",
        ],
        env,
    )

    payload = json.loads(result.stdout)
    assert len(payload) == 1
    entry = payload[0]
    assert entry["path"] == str(track)
    assert entry["destination"] == destination.destination
    assert entry["category"] == "Media/Music"
    assert entry["metadata"]["artist"] == "Unit Test"


def test_cli_analyze_full_exits_when_ai_required_but_unavailable(tmp_path):
    source_dir = tmp_path / "src"
    target_dir = tmp_path / "target"
    report_dir = tmp_path / "reports"
    db_path = tmp_path / "catalog.sqlite"
    source_dir.mkdir()
    target_dir.mkdir()
    report_dir.mkdir()

    sample = source_dir / "note.txt"
    sample.write_text("hello world")

    env = _env_for_cli(db_path, source_dir, target_dir, report_dir)
    result = _run_cli_expect_failure(
        [
            str(sample),
            "--ollama-url",
            "http://127.0.0.1:9",  # unused port -> unavailable
        ],
        env,
    )
    assert "No available AI workers" in result.stderr or "Failed to initialize AI workers" in result.stderr
