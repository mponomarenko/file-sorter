import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator, Sequence

from .config import AppConfig
from .classification_records import ClassificationRecord
from .utils import log


class Database:
    """SQLite-backed metadata store bound to a specific AppConfig."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        con = sqlite3.connect(self.cfg.DB_PATH)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        schema = """
        CREATE TABLE IF NOT EXISTS files (
          id INTEGER PRIMARY KEY,
          path TEXT UNIQUE,
          size INTEGER,
          mtime REAL,
          mime TEXT,
          hash TEXT,
          category TEXT,
          rule_category TEXT,
          ai_category TEXT,
          metadata_json TEXT,
          preview TEXT,
          file_json TEXT,
          dest TEXT,
          status TEXT,
          note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_hash ON files(hash);
        CREATE INDEX IF NOT EXISTS idx_size ON files(size);
        CREATE TABLE IF NOT EXISTS folder_hashes (
          folder_path TEXT PRIMARY KEY,
          folder_hash TEXT,
          file_count INTEGER,
          byte_size INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_folder_hash ON folder_hashes(folder_hash);
        CREATE TABLE IF NOT EXISTS folder_actions (
          folder_path TEXT PRIMARY KEY,
          action TEXT NOT NULL,
          decision_source TEXT,
          decided_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_folder_path ON folder_actions(folder_path);
        """
        for stmt in schema.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                con.execute(stmt)
        existing_columns = {row[1] for row in con.execute("PRAGMA table_info(files)")}
        for name, col_type in [
            ("rule_category", "TEXT"),
            ("ai_category", "TEXT"),
            ("metadata_json", "TEXT"),
            ("preview", "TEXT"),
            ("file_json", "TEXT"),
        ]:
            if name not in existing_columns:
                con.execute(f"ALTER TABLE files ADD COLUMN {name} {col_type}")
        con.commit()
        con.close()
        log.debug(f"db: init path={self.cfg.DB_PATH}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.cfg.DB_PATH, check_same_thread=False)
        try:
            yield con
        finally:
            con.close()

    def bulk_insert(self, files_rows: Iterable[Sequence]) -> int:
        """Insert files and return the number of rows actually inserted (excluding duplicates)."""
        with self.connect() as con:
            cur = con.cursor()
            rows_list = list(files_rows)
            cur.executemany(
                "INSERT OR IGNORE INTO files(path,size,mtime,mime,hash,status) VALUES(?,?,?,?,?,?)",
                rows_list,
            )
            inserted = cur.rowcount
            con.commit()
            return inserted

    def count_all_files(self) -> int:
        """Return the total number of files in the database."""
        with self.connect() as con:
            cur = con.cursor()
            result = cur.execute("SELECT COUNT(*) FROM files").fetchone()
            return result[0] if result else 0

    def update_category_dest(self, rows: Iterable[ClassificationRecord]) -> None:
        with self.connect() as con:
            cur = con.cursor()
            prepared: list[tuple[str | None, str | None, str | None, str | None, str | None, str | None, str | None, str]] = []
            for entry in rows:
                prepared.append(entry.as_db_tuple())
            cur.executemany(
                "UPDATE files SET category=?, dest=?, rule_category=?, ai_category=?, metadata_json=?, preview=?, file_json=? WHERE path=?",
                prepared,
            )
            con.commit()

    def update_status(self, rows: Iterable[Sequence]) -> None:
        with self.connect() as con:
            cur = con.cursor()
            cur.executemany("UPDATE files SET status=?, note=? WHERE path=?", rows)
            con.commit()

    def select_unclassified(self, limit: int | None = None) -> list[tuple[str, str, int]]:
        with self.connect() as con:
            cur = con.cursor()
            # Process files from shallowest to deepest paths
            # This ensures parent folder decisions are made before classifying all children
            # Skip files in folders with KEEP action (they inherit)
            query = """
                SELECT f.path, f.mime, f.size 
                FROM files f
                WHERE f.category IS NULL 
                  AND f.hash IS NOT NULL 
                  AND f.status='scanned'
                  AND NOT EXISTS (
                    SELECT 1 FROM folder_actions fa
                    WHERE fa.action = 'keep'
                      AND (f.path LIKE fa.folder_path || '/%' OR f.path LIKE fa.folder_path)
                  )
                ORDER BY length(f.path) - length(replace(f.path, '/', '')), f.path
            """
            if limit is not None:
                query += " LIMIT ?"
                return cur.execute(query, (int(limit),)).fetchall()
            return cur.execute(query).fetchall()

    def select_planned_moves(self) -> list[tuple[str, str]]:
        with self.connect() as con:
            cur = con.cursor()
            return cur.execute(
                "SELECT path, dest FROM files WHERE dest IS NOT NULL AND status='scanned'"
            ).fetchall()

    def select_planned_with_hash(self) -> list[tuple[str, str, str]]:
        with self.connect() as con:
            cur = con.cursor()
            return cur.execute(
                "SELECT path, dest, hash FROM files WHERE dest IS NOT NULL AND status='scanned'"
            ).fetchall()

    def select_planned_details(self) -> list[tuple[str, str, int, str, str, str | None, str | None]]:
        with self.connect() as con:
            cur = con.cursor()
            return cur.execute(
                "SELECT path, dest, size, category, mime, preview, file_json FROM files WHERE dest IS NOT NULL AND status='scanned'"
            ).fetchall()

    def iter_all_files_for_folder_hashing(self) -> Iterator[tuple[str, str, int]]:
        with self.connect() as con:
            cur = con.cursor()
            for row in cur.execute(
                "SELECT path, hash, size FROM files WHERE status='scanned'"
            ):
                yield row

    def upsert_folder_hashes(self, rows: Iterable[Sequence]) -> None:
        with self.connect() as con:
            cur = con.cursor()
            cur.executemany(
                "INSERT INTO folder_hashes(folder_path, folder_hash, file_count, byte_size) VALUES(?,?,?,?) "
                "ON CONFLICT(folder_path) DO UPDATE SET folder_hash=excluded.folder_hash, "
                "file_count=excluded.file_count, byte_size=excluded.byte_size",
                rows,
            )
            con.commit()

    def select_duplicate_folders(self) -> list[tuple[str, list[str]]]:
        with self.connect() as con:
            cur = con.cursor()
            rows = cur.execute(
                "SELECT folder_hash, GROUP_CONCAT(folder_path,'\x1f') AS paths, COUNT(*) AS n "
                "FROM folder_hashes "
                "WHERE folder_hash IS NOT NULL AND folder_hash<>'' "
                "GROUP BY folder_hash HAVING n>1"
            ).fetchall()
            out: list[tuple[str, list[str]]] = []
            for folder_hash, joined, _ in rows:
                paths = joined.split("\x1f") if joined else []
                out.append((folder_hash, paths))
            return out

    def save_folder_actions(self, actions: dict[str, str], decisions: dict[str, str]) -> None:
        """Save folder action decisions to database."""
        import time
        with self.connect() as con:
            timestamp = int(time.time())
            for folder_path, action in actions.items():
                decision_source = decisions.get(folder_path, "unknown")
                con.execute(
                    "INSERT OR REPLACE INTO folder_actions (folder_path, action, decision_source, decided_at) VALUES (?, ?, ?, ?)",
                    (folder_path, action, decision_source, timestamp)
                )
            con.commit()

    def get_folder_actions(self) -> dict[str, str]:
        """Retrieve all decided folder actions from database."""
        with self.connect() as con:
            cur = con.cursor()
            rows = cur.execute("SELECT folder_path, action FROM folder_actions").fetchall()
            return {folder_path: action for folder_path, action in rows}

    def iter_all(self) -> Iterator[tuple]:
        with self.connect() as con:
            cur = con.cursor()
            for row in cur.execute(
                "SELECT path,size,mime,hash,category,dest,rule_category,ai_category,metadata_json,preview,file_json,status,note FROM files"
            ):
                yield row
