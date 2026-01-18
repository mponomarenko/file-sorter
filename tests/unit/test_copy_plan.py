import os
import sys
from pathlib import Path

import pytest

THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.copy_plan import build_copy_script
from app.classifiers import RulesClassifier


def _write_rules(tmp_path: Path) -> Path:
    lines = [
        "# Path Glob, Mime Glob, Category Path, Folder Action, AI",
        "^/src/projects/keepdir/.*$,.*,Software/Source_Code,keep,final",
        "^/src/uploads/.*\\.jpg$,image/.+,Media/Photos,keep,final",
        "^.*$,.*,Unknown,disaggregate,ai",
    ]
    data = "\n".join(lines) + "\n"
    rules_path = tmp_path / "rules.csv"
    rules_path.write_text(data, encoding="utf-8")
    return rules_path


def test_build_copy_script_generates_commands(tmp_path: Path):
    rules_path = _write_rules(tmp_path)
    classifier = RulesClassifier(rules_path)
    assert classifier.ensure_available()

    planned_items = [
        ("/src/projects/keepdir/readme.md", "/dest/Software/Source_Code/keepdir/readme.md", 1024, "Software/Source_Code", "text/markdown"),
        ("/src/uploads/img1.jpg", "/dest/Media/Photos/2024/01-02/img1.jpg", 2 * 1024 * 1024, "Media/Photos", "image/jpeg"),
        ("/src/uploads/img2.jpg", "/dest/Media/Photos/2024/01-02/img2.jpg", 3 * 1024 * 1024, "Media/Photos", "image/jpeg"),
    ]

    script_path = tmp_path / "copy.sh"
    result = build_copy_script(planned_items, classifier, script_path)

    assert result is not None
    content = script_path.read_text(encoding="utf-8")
    assert "rsync -a" in content
    assert "projects/keepdir" in content
    assert "img1.jpg" in content and "img2.jpg" in content
    assert "Batch size" in content
    assert "mkdir -p" in content
    # Ensure script is executable
    mode = script_path.stat().st_mode
    assert mode & 0o111


def test_build_copy_script_handles_empty(tmp_path: Path):
    rules_path = _write_rules(tmp_path)
    classifier = RulesClassifier(rules_path)
    assert classifier.ensure_available()

    script_path = tmp_path / "copy.sh"
    result = build_copy_script([], classifier, script_path)
    assert result is None
    assert not script_path.exists()
