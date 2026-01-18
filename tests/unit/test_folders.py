import sys
import tempfile
from pathlib import Path
from dataclasses import replace

from app.config import AppConfig
from app.db import Database
from app.folders import FolderAnalyzer, _hash_folder_text, _all_ancestors

# Ensure we can import `app.*` by adding `file-sorter` to sys.path
THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))


def _setup_temp_db():
    td = tempfile.TemporaryDirectory()
    db_dir = Path(td.name)
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(Path(db_dir, "test.sqlite"))
    cfg = replace(AppConfig.from_env(), DB_PATH=db_path)
    database = Database(cfg)
    analyzer = FolderAnalyzer(cfg, database)
    return td, database, analyzer


def test_compute_folder_hashes_and_detect_duplicates():
    td, database, analyzer = _setup_temp_db()
    try:
        files = [
            ("/root1/Proj/docs/readme.txt", 10, 0.0, "text/plain", "H1", "scanned"),
            ("/root1/Proj/docs/spec.txt", 20, 0.0, "text/plain", "H2", "scanned"),
            ("/root2/Proj/docs/readme.txt", 10, 0.0, "text/plain", "H1", "scanned"),
            ("/root2/Proj/docs/spec.txt", 20, 0.0, "text/plain", "H2", "scanned"),
            ("/root3/Other/x.bin", 5, 0.0, "application/octet-stream", "HX", "scanned"),
        ]
        database.bulk_insert(files)

        analyzer.compute_folder_hashes()

        dups = database.select_duplicate_folders()
        all_groups = [set(paths) for (_, paths) in dups]
        assert any({"/root1/Proj", "/root2/Proj"}.issubset(g) for g in all_groups)
    finally:
        td.cleanup()


def test_hash_folder_text():
    assert _hash_folder_text([]) == "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"
    lines = ["file.txt|abcd1234"]
    h1 = _hash_folder_text(lines)
    assert isinstance(h1, str)
    assert len(h1) == 64
    lines1 = ["a.txt|hash1", "b.txt|hash2"]
    lines2 = ["b.txt|hash2", "a.txt|hash1"]
    lines1.sort(); lines2.sort()
    assert _hash_folder_text(lines1) == _hash_folder_text(lines2)


def test_all_ancestors():
    p = Path("mydir/folder/file.txt")
    ancestors = _all_ancestors(p)
    assert len(ancestors) > 0
    assert any("folder" in str(x) for x in ancestors)
    assert any("mydir" in str(x) for x in ancestors)

    p = Path("/fixtures/paths/home/user/docs/file.txt")
    ancestors = _all_ancestors(p)
    assert len(ancestors) > 0
    assert any("docs" in str(x) for x in ancestors)
    assert any("user" in str(x) for x in ancestors)

    p = Path("./file.txt")
    ancestors = _all_ancestors(p)
    assert len(ancestors) > 0


def test_find_duplicate_folders():
    td, database, analyzer = _setup_temp_db()
    try:
        files = [
            ("/test1/big/file1.txt", 1000, 0.0, "text/plain", "H1", "scanned"),
            ("/test1/big/file2.txt", 2000, 0.0, "text/plain", "H2", "scanned"),
            ("/test2/big/file1.txt", 1000, 0.0, "text/plain", "H1", "scanned"),
            ("/test2/big/file2.txt", 2000, 0.0, "text/plain", "H2", "scanned"),
        ]
        database.bulk_insert(files)

        analyzer.compute_folder_hashes()
        dups = analyzer.find_duplicate_folders()

        test_group = next((g for g in dups if "/test1/big" in g["paths"] and "/test2/big" in g["paths"]), None)
        assert test_group is not None
        assert test_group["size"] == 6000
        assert {"/test1/big", "/test2/big"} == set(test_group["paths"])
        assert "hash" in test_group and isinstance(test_group["hash"], str)
    finally:
        td.cleanup()
