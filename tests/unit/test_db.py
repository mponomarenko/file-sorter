import sys
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
from app.db import Database
from app.classification_records import ClassificationRecord


def test_db_basic_crud():
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td, "test.sqlite"))
        cfg = replace(AppConfig.from_env(), DB_PATH=db_path)
        database = Database(cfg)

        files = [
            ("/a/one.txt", 10, 1_700_000_001.0, "text/plain", "h1", "scanned"),
            ("/b/two.mp3", 20, 1_700_000_002.0, "audio/mpeg", "h2", "scanned"),
        ]
        database.bulk_insert(files)

        uc = database.select_unclassified()
        # Now returns (path, mime, size) tuples
        assert sorted(uc) == sorted([(p, m, sz) for (p, sz, _, m, _, _) in files])

        record = ClassificationRecord(
            category_path=CategoryPath("Media", "Music"),
            destination="/target/Media/Music/two.mp3",
            path="/b/two.mp3",
            rule_category=CategoryPath("Media", "Music"),
            ai_category=CategoryPath("Media", "Music"),
            metadata_json='{"score": 0.95}',
            preview="preview text",
            file_json='{"physical_path":"/b/two.mp3"}',
        )
        database.update_category_dest([record])
        moves = database.select_planned_moves()
        assert moves == [("/b/two.mp3", "/target/Media/Music/two.mp3")]

        database.update_status([("planned", "-> /t", "/a/one.txt")])

        rows = list(database.iter_all())
        assert len(rows) == 2
        row_map = {r[0]: r for r in rows}
        b_row = row_map["/b/two.mp3"]
        assert b_row[4] == "Media/Music"
        assert b_row[5] == "/target/Media/Music/two.mp3"
        assert b_row[6] == "Media/Music"
        assert b_row[7] == "Media/Music"
        assert b_row[8] == '{"score": 0.95}'
        assert b_row[9] == "preview text"
        assert b_row[10] == '{"physical_path":"/b/two.mp3"}'


def test_update_category_dest_accepts_classification_record():
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td, "test.sqlite"))
        cfg = replace(AppConfig.from_env(), DB_PATH=db_path)
        database = Database(cfg)
        database.bulk_insert([
            ("/docs/readme.txt", 5, 1_700_000_010.0, "text/plain", "h3", "scanned"),
        ])

        record = ClassificationRecord(
            category_path=CategoryPath("Docs", "Readme"),
            destination="/target/Docs/Readme/readme.txt",
            path="/docs/readme.txt",
            rule_category=CategoryPath("Docs"),
            ai_category=None,
            metadata_json='{"source": "test"}',
            preview="hello world",
            file_json='{"physical_path":"/docs/readme.txt"}',
        )
        database.update_category_dest([record])

        rows = list(database.iter_all())
        assert len(rows) == 1
        stored = rows[0]
        assert stored[4] == "Docs/Readme"
        assert stored[5] == "/target/Docs/Readme/readme.txt"
        assert stored[6] == "Docs"
        assert stored[7] is None
        assert stored[8] == '{"source": "test"}'
        assert stored[9] == "hello world"

