import time
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional, TypeVar

T = TypeVar("T")
MAX_LATENCY_SECONDS = 120.0


@dataclass
class MetricSnapshot:
    requests: int
    success: int
    failure: int
    latency_ms: float
    first_started: Optional[float]
    last_started: Optional[float]

    @property
    def success_rate(self) -> float:
        if self.requests == 0:
            return 0.0
        return self.success / self.requests

    @property
    def avg_latency_ms(self) -> float:
        if self.success == 0:
            return 0.0
        return self.latency_ms / self.success


class Metric:
    def __init__(self) -> None:
        self._success = 0
        self._failure = 0
        self._latency_ms = 0.0
        self._first_started: Optional[float] = None
        self._last_started: Optional[float] = None

    def reset(self) -> None:
        self._success = 0
        self._failure = 0
        self._latency_ms = 0.0
        self._first_started = None
        self._last_started = None

    def record(self, *, started_at: float, duration_s: float, success: bool) -> None:
        duration = max(0.0, min(duration_s, MAX_LATENCY_SECONDS))
        self._latency_ms += duration * 1000.0
        if success:
            self._success += 1
        else:
            self._failure += 1
        if self._first_started is None:
            self._first_started = started_at
        self._last_started = started_at

    def snapshot(self) -> MetricSnapshot:
        requests = self._success + self._failure
        return MetricSnapshot(
            requests=requests,
            success=self._success,
            failure=self._failure,
            latency_ms=self._latency_ms,
            first_started=self._first_started,
            last_started=self._last_started,
        )

    def timed(self, fn: Callable[[], T]) -> T:
        started = time.time()
        try:
            result = fn()
            self.record(started_at=started, duration_s=time.time() - started, success=True)
            return result
        except Exception:
            self.record(started_at=started, duration_s=time.time() - started, success=False)
            raise

    async def timed_async(self, fn: Callable[[], Awaitable[T]]) -> T:
        started = time.time()
        try:
            result = await fn()
            self.record(started_at=started, duration_s=time.time() - started, success=True)
            return result
        except Exception:
            self.record(started_at=started, duration_s=time.time() - started, success=False)
            raise
