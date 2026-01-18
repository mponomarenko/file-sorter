import os
import logging
import structlog
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable, TypeVar, Union
from pathlib import Path
from datetime import datetime, timezone

from .config import AppConfig
config = AppConfig.from_env()
from .exceptions import FileOperationError

# Setup structured logging
_vlog = config.VLOG
_level_name = config.LOG_LEVEL
if _vlog:
    _level_name = "DEBUG"

logging.basicConfig(level=_level_name)


def _iso_timestamp(_, __, event_dict):
    """Use timezone-aware UTC timestamps to avoid datetime.utcnow deprecation warnings."""
    event_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
    return event_dict


structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,  # Filter based on log level BEFORE processing
        _iso_timestamp,
        structlog.stdlib.add_log_level,
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(colors=True)
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

log = structlog.get_logger("cleaner")

# Set the level on the underlying stdlib logger that structlog wraps
# This ensures log.debug() calls are actually filtered at the source
logging.getLogger("cleaner").setLevel(_level_name)

# Ensure httpx/httpcore debug logs are suppressed unless an explicit TRACE level is requested.
# We intentionally keep httpx/httpcore at WARNING even when the app is running in DEBUG/VLOG
# mode so that noisy http debug messages don't fill logs. If a user sets
# LOG_LEVEL=TRACE in the environment, we allow httpx/httpcore DEBUG messages.
httpx_logger = logging.getLogger("httpx")
httpcore_logger = logging.getLogger("httpcore")
if config.LOG_LEVEL == "TRACE":
    httpx_logger.setLevel(logging.DEBUG)
    httpcore_logger.setLevel(logging.DEBUG)
else:
    httpx_logger.setLevel(logging.WARNING)
    httpcore_logger.setLevel(logging.WARNING)

# Type var for generic operations
T = TypeVar('T')

def safe_file_op(operation: Callable[[], T],
                 path: Union[str, Path],
                 default: Optional[T] = None,
                 log_error: bool = True) -> Optional[T]:
    """Safely execute a file operation with standard error handling"""
    try:
        return operation()
    except PermissionError as e:
        if log_error:
            log.warning("permission_error", path=str(path), error=str(e))
        raise FileOperationError(f"Permission denied: {path}") from e
    except FileNotFoundError as e:
        if log_error:
            log.warning("file_not_found", path=str(path), error=str(e))
        raise FileOperationError(f"File not found: {path}") from e
    except Exception as e:
        if log_error:
            log.error("unexpected_error", path=str(path), error=str(e))
        raise FileOperationError(f"Unexpected error on {path}: {e}") from e

# Concurrency pools (thread pools are fine: hashing is partly I/O-bound, and blake3 is fast)
scan_pool = ThreadPoolExecutor(max_workers=config.SCAN_WORKERS)
hash_pool = ThreadPoolExecutor(max_workers=config.HASH_WORKERS)
move_pool = ThreadPoolExecutor(max_workers=config.MOVE_WORKERS)
