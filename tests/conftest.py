import os
from pathlib import Path

import pytest


def _require_ollama_env() -> None:
    root = Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    if not env_file.is_file():
        raise RuntimeError("Missing .env; tests require OLLAMA_URL to be defined in .env")

    ollama_value: str | None = None
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        if key.strip() == "OLLAMA_URL":
            ollama_value = val.strip().strip('"').strip("'")
            break

    if not ollama_value:
        raise RuntimeError(".env must define OLLAMA_URL for tests")

    # Mirror .env into the environment for tests
    os.environ.setdefault("OLLAMA_URL", ollama_value)


_require_ollama_env()
