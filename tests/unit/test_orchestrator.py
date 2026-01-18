import sys
import tempfile
from pathlib import Path
from dataclasses import replace

# Ensure we can import `app.*`
THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.config import AppConfig
from app.orchestrator import Orchestrator
from app.media import MediaHelper
from app.planner import Planner


class FakeDB:
    def __init__(self):
        self.inserted = []
        self._rows = []
        self.last_status = None

    def bulk_insert(self, rows):
        self.inserted.extend(rows)
        return len(rows)

    def iter_all(self):
        return iter(self._rows)

    def iter_all_files_for_folder_hashing(self):
        return []

    def upsert_folder_hashes(self, rows):
        pass

    def select_duplicate_folders(self):
        return []

    def select_planned_moves(self):
        return []

    def select_planned_details(self):
        return []

    def update_status(self, rows):
        self.last_status = rows
    
    def count_all_files(self):
        """Return the number of files in the database."""
        return len(self._rows)
    
    def get_folder_actions(self):
        """Return empty dict for folder actions (test stub)."""
        return {}
    
    def save_folder_actions(self, actions, decisions):
        """No-op for tests."""
        pass


class StubFolders:
    def __init__(self):
        self.computed = False
        self.duplicates = []

    def compute_folder_hashes(self, batch_size: int = 5000):
        self.computed = True

    def find_duplicate_folders(self):
        return self.duplicates


def _make_orchestrator(cfg: AppConfig, db, folders) -> Orchestrator:
    media = MediaHelper(cfg)
    planner = Planner(cfg, db, media)
    return Orchestrator(cfg, database=db, media=media, planner=planner, folders=folders)


def test_scan_paths_inserts_hashes_and_handles_empty_files():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td, "src"); src.mkdir()
        f1 = Path(src, "a.txt"); f1.write_text("hello")
        f2 = Path(src, "empty.bin"); f2.write_bytes(b"")
        cfg = replace(AppConfig.from_env(), SOURCES=[str(src)], DB_PATH=str(Path(td, "db.sqlite")))

        fake_db = FakeDB()
        folders = StubFolders()
        orch = _make_orchestrator(cfg, fake_db, folders)

        orch.scan_paths()

        found = {p: (h) for (p, s, m, mi, h, st) in fake_db.inserted}
        assert found[str(f1)] and len(found[str(f1)]) == 64
        assert found[str(f2)] == ""
        assert folders.computed is True


def test_write_report_creates_file():
    with tempfile.TemporaryDirectory() as td:
        reports_dir = Path(td, "reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        cfg = replace(AppConfig.from_env(), REPORT_DIR=str(reports_dir), SOURCES=[str(Path(td, "src"))], DB_PATH=str(Path(td, "db.sqlite")))

        fake_db = FakeDB()
        fake_db._rows = [
            ("/a", 1, "text/plain", "h", None, None, None, None, None, None, None, "scanned", None),
            ("/b", 2, "audio/mpeg", "h2", "music", "/t/Music/b", "music", "music", '{"meta": true}', "snippet", "{}", "planned", "->"),
        ]
        folders = StubFolders()
        folders.duplicates = [{"hash": "abc", "paths": ["/a", "/b"], "size": 42}]
        orch = _make_orchestrator(cfg, fake_db, folders)

        orch.write_report()

        out_dir = Path(cfg.REPORT_DIR)
        files = list(out_dir.glob("cleanup_report_*.csv"))
        assert files, "Expected a report file to be created"
        content = files[0].read_text(encoding="utf-8").strip().splitlines()
        assert "metadata_json" in content[0]
        assert "path" in content[0] and "dest" in content[0]
