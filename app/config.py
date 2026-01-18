import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

from .categories import Categories

DEFAULT_CATEGORIES_PATH = Path(__file__).resolve().parent / "data" / "categories.csv"

@dataclass
class AppConfig:
    # File Processing
    MAX_CONTENT_PEEK: int = 1024
    DB_BATCH_SIZE: int = 500
    RELINK_WITH_REFLINK: bool = True
    STRIP_DIRS: List[str] = field(default_factory=list)

    # Workers
    SCAN_WORKERS: int = 4
    HASH_WORKERS: int = 4
    MOVE_WORKERS: int = 2
    PREVIEW_WORKERS: int = 1  # For OCR and text extraction - no concurrency to prevent OOM
    OCR_TIMEOUT_SECONDS: int = 30

    # Ollama
    OLLAMA_URL: List[str] = field(default_factory=list)
    OLLAMA_WORKERS: int = 4
    OLLAMA_TIMEOUT: int = 120
    OLLAMA_RETRIES: int = 3
    OLLAMA_BACKOFF: float = 1.5
    OLLAMA_THROTTLE_SECONDS: float = 0.0
    
    # Paths
    DB_PATH: str = "/work/catalog.sqlite"
    MAIN_TARGET: str = "/target"
    REPORT_DIR: str = "/target/_reports"
    SOURCES: List[str] = field(default_factory=list)
    SOURCE_WRAPPER_REGEX: str = "src\\d+"

    # Classification
    CLASSIFIER_KIND: str = "ollama"
    CATEGORIES_PATH: str = field(default_factory=lambda: str(DEFAULT_CATEGORIES_PATH))
    categories: Categories = field(init=False, repr=False)
    # Logging / misc
    VLOG: bool = False
    LOG_LEVEL: str = "INFO"
    MODE: str = "all"

    def __post_init__(self):
        if self.STRIP_DIRS is None:
            self.STRIP_DIRS = []
        if self.SOURCES is None or not self.SOURCES:
            raise ValueError("SOURCES must be configured and non-empty. Set SOURCES environment variable or ensure it's passed to config.")
        if (self.CLASSIFIER_KIND != "manual") and (not self.OLLAMA_URL):
            raise ValueError("OLLAMA_URL must be configured for AI classifiers. Set OLLAMA_URL or CLASSIFIER=manual.")
        path = Path(self.CATEGORIES_PATH)
        if not path.is_file():
            raise FileNotFoundError(f"Categories file not found: {path}")
        self.categories = Categories.from_source(path)


    @classmethod
    def from_env(cls) -> 'AppConfig':
        """Create configuration from environment variables"""
        raw_url_value = os.getenv("OLLAMA_URL", "")
        raw_urls = [u.strip() for u in raw_url_value.split(",") if u.strip()]
        classifier_kind = os.getenv("CLASSIFIER", "ollama").lower()
        if not raw_urls and classifier_kind != "manual":
            raise ValueError("OLLAMA_URL is required unless CLASSIFIER=manual")
        return cls(
            MAX_CONTENT_PEEK=int(os.getenv("MAX_CONTENT_PEEK", "1024")),
            DB_BATCH_SIZE=int(os.getenv("DB_BATCH_SIZE", "500")),
            RELINK_WITH_REFLINK=os.getenv("RELINK_WITH_REFLINK", "true").lower() == "true",
            STRIP_DIRS=[d.strip() for d in os.getenv("STRIP_DIRS", "").split(",") if d.strip()],
            SCAN_WORKERS=int(os.getenv("SCAN_WORKERS", "4")),
            HASH_WORKERS=int(os.getenv("HASH_WORKERS", "4")),
            MOVE_WORKERS=int(os.getenv("MOVE_WORKERS", "2")),
            PREVIEW_WORKERS=int(os.getenv("PREVIEW_WORKERS", "1")),
            OCR_TIMEOUT_SECONDS=int(os.getenv("OCR_TIMEOUT_SECONDS", "30")),
            VLOG=os.getenv("VLOG", "").lower() in ("1", "true", "yes", "on"),
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO").upper(),
            MODE=os.getenv("MODE", "all").lower(),
            OLLAMA_URL=raw_urls,
            OLLAMA_WORKERS=int(os.getenv("OLLAMA_WORKERS", "4")),
            OLLAMA_TIMEOUT=int(os.getenv("OLLAMA_TIMEOUT", "120")),
            OLLAMA_RETRIES=int(os.getenv("OLLAMA_RETRIES", "3")),
            OLLAMA_BACKOFF=float(os.getenv("OLLAMA_BACKOFF", "1.5")),
            OLLAMA_THROTTLE_SECONDS=float(os.getenv("OLLAMA_THROTTLE_SECONDS", "0")),
            DB_PATH=os.getenv("DB_PATH", "/work/catalog.sqlite"),
            MAIN_TARGET=os.getenv("MAIN_TARGET", "/target"),
            REPORT_DIR=os.getenv("REPORT_DIR", "/target/_reports"),
            SOURCES=[s for s in os.getenv("SOURCES","/sources/src1").split(",") if s],
            SOURCE_WRAPPER_REGEX=os.getenv("SOURCE_WRAPPER_REGEX", "src\\d+"),
            CLASSIFIER_KIND=classifier_kind,
            CATEGORIES_PATH=os.getenv("CATEGORIES_PATH", str(DEFAULT_CATEGORIES_PATH)),
        )

    def ollama_endpoints(self) -> List[Tuple[str, int, str]]:
        """Parse OLLAMA_URL entries into (url, workers, model) tuples.
        
        Format: url|workers|model
        Examples:
            http://localhost:11434|4|gpt-oss:20b
            http://lmstudio:1234|2|gpt-oss-20b
            http://ollama:11434  (uses default workers, requires model)
        
        Returns:
            List of (url, workers, model_name) tuples
        """
        endpoints: List[Tuple[str, int, str]] = []
        default_workers = max(1, int(self.OLLAMA_WORKERS))
        
        for entry in self.OLLAMA_URL or []:
            entry = entry.strip()
            if not entry:
                continue
            
            parts = entry.split("|")
            url = parts[0].strip()
            workers = default_workers
            model = None
            
            if len(parts) >= 2:
                try:
                    workers = max(1, int(parts[1].strip()))
                except ValueError:
                    workers = default_workers
            
            if len(parts) >= 3:
                model = parts[2].strip()
            
            if not model:
                raise ValueError(
                    f"Model name required in OLLAMA_URL entry: {entry}\n"
                    f"Format: url|workers|model (e.g., http://localhost:11434|4|gpt-oss:20b)"
                )
            
            endpoints.append((url, workers, model))
        
        return endpoints

# Global config instance
config = AppConfig.from_env()
