import time
from dataclasses import dataclass, field
from typing import List
from datetime import datetime

from ..categories import CategoryPath, UNKNOWN_CATEGORY
from ..utils import log
from ..metrics import Metric, MetricSnapshot
from ..folder_action import FolderAction, FolderActionRequest
from .base import Classifier, ClassifierResponse, FolderActionResponse


@dataclass
class Worker:
    classifier: Classifier
    name: str
    total: Metric = field(default_factory=Metric)
    window: Metric = field(default_factory=Metric)
    last_used: float = 0.0
    consecutive_failures: int = 0
    current_weight: float = 0.0
    cooldown_until: float = 0.0
    in_flight: int = 0


class MultiplexedClassifier(Classifier):
    def __init__(self, workers: List[Classifier], stats_interval: int = 900, failure_cooldown: float = 2.0):
        self.workers = [Worker(worker, name=worker.display_name()) for worker in workers]
        self.stats_interval = stats_interval
        self.failure_cooldown = failure_cooldown
        self._default_weight = 5.0
        self._last_selected: Worker | None = None
        self.last_stats_dump = 0.0
        self.requests_since_dump = 0

    async def classify(self, name: str, rel_path: str, mime: str, sample: str, hint: dict | None = None) -> ClassifierResponse:
        
        worker = self._select_worker()
        worker.in_flight += 1
        started = time.time()
        worker.last_used = started
        try:
            result = await worker.classifier.classify(name, rel_path, mime, sample, hint)
            duration = time.time() - started
            worker.total.record(started_at=started, duration_s=duration, success=True)
            worker.window.record(started_at=started, duration_s=duration, success=True)
            worker.consecutive_failures = 0
            
            # Add multiplexer metrics to the response
            metrics = result.metrics.copy() if result.metrics else {}
            metrics.update({
                "multiplexer": {
                    "worker": worker.name,
                    "duration": duration,
                    "queued_tasks": worker.in_flight - 1
                }
            })
            return ClassifierResponse(
                path=result.path,
                metrics=metrics,
                error=result.error,
                error_context=result.error_context,
            )
        except Exception as exc:
            duration = time.time() - started
            worker.total.record(started_at=started, duration_s=duration, success=False)
            worker.window.record(started_at=started, duration_s=duration, success=False)
            worker.consecutive_failures += 1
            backoff = min(5, worker.consecutive_failures)
            worker.cooldown_until = time.time() + (self.failure_cooldown * backoff)
            log.error("worker_classify_error", error=str(exc))
            
            # UNKNOWN_CATEGORY is already imported
            return ClassifierResponse(
                path=UNKNOWN_CATEGORY,
                metrics={
                    "error": str(exc),
                    "multiplexer": {
                        "worker": worker.name,
                        "duration": duration,
                        "queued_tasks": worker.in_flight - 1,
                        "consecutive_failures": worker.consecutive_failures
                    }
                },
                error=exc,
                error_context={"worker": worker.name},
            )
        finally:
            self.requests_since_dump += 1
            self._maybe_dump_stats()
            worker.in_flight = max(0, worker.in_flight - 1)

    def advise_folder_action(self, request: FolderActionRequest) -> FolderActionResponse:
        """Multiplex folder action decision across workers.
        
        Returns worker's response (decision or delegation).
        """
        
        worker = self._select_worker()
        worker.in_flight += 1
        started = time.time()
        worker.last_used = started
        try:
            result = worker.classifier.advise_folder_action(request)
            duration = time.time() - started
            worker.total.record(started_at=started, duration_s=duration, success=True)
            worker.window.record(started_at=started, duration_s=duration, success=True)
            worker.consecutive_failures = 0
            return result
        except Exception as exc:
            duration = time.time() - started
            worker.total.record(started_at=started, duration_s=duration, success=False)
            worker.window.record(started_at=started, duration_s=duration, success=False)
            worker.consecutive_failures += 1
            backoff = min(5, worker.consecutive_failures)
            worker.cooldown_until = time.time() + (self.failure_cooldown * backoff)
            log.error("worker_folder_action_error", error=str(exc))
            action = request.rule_hint or FolderAction.DISAGGREGATE
            return FolderActionResponse.decision(action, reason="multiplexer:error")
        finally:
            self.requests_since_dump += 1
            self._maybe_dump_stats()
            worker.in_flight = max(0, worker.in_flight - 1)

    def _maybe_dump_stats(self) -> None:
        now = time.time()
        if self.requests_since_dump < 1000 and now - self.last_stats_dump < self.stats_interval:
            return

        stats_payload = {}
        aggregate_total = MetricSnapshot(0, 0, 0, 0.0, None, None)
        aggregate_window = MetricSnapshot(0, 0, 0, 0.0, None, None)

        for idx, worker in enumerate(self.workers):
            total_snapshot = worker.total.snapshot()
            window_snapshot = worker.window.snapshot()
            stats_payload[f"worker_{idx}"] = {
                "name": worker.name,
                "lifetime": self._snapshot_dict(total_snapshot, worker.last_used),
                "window": self._snapshot_dict(window_snapshot, worker.last_used),
            }
            aggregate_total = self._combine_snapshots(aggregate_total, total_snapshot)
            aggregate_window = self._combine_snapshots(aggregate_window, window_snapshot)

        stats_payload["total"] = {
            "name": "all",
            "lifetime": self._snapshot_dict(aggregate_total, now),
            "window": self._snapshot_dict(aggregate_window, now),
        }

        log.info(
            "classifier_stats",
            stats=stats_payload,
            elapsed_seconds=round(now - self.last_stats_dump, 1),
            requests_processed=self.requests_since_dump,
        )

        self.last_stats_dump = now
        self.requests_since_dump = 0
        for worker in self.workers:
            worker.window.reset()

    def _select_worker(self) -> Worker:
        available = self._available_workers()
        unused = [worker for worker in available if worker.total.snapshot().requests == 0 and worker.in_flight == 0]
        if unused:
            selected = unused[0]
            selected.current_weight = 0.0
            self._last_selected = selected
            return selected

        total_weight = 0.0
        chosen: Worker | None = None
        for worker in available:
            weight = self._worker_weight(worker)
            worker.current_weight += weight
            total_weight += weight
            if chosen is None or worker.current_weight > chosen.current_weight:
                chosen = worker
        assert chosen is not None
        if total_weight > 0:
            chosen.current_weight -= total_weight
        self._last_selected = chosen
        return chosen

    def _available_workers(self) -> List[Worker]:
        now = time.time()
        primary: List[Worker] = []
        fallback: List[Worker] = []
        for worker in self.workers:
            if worker.cooldown_until and now < worker.cooldown_until:
                continue
            snapshot = worker.total.snapshot()
            if snapshot.requests == 0 and worker.in_flight == 0:
                primary.append(worker)
                continue
            if snapshot.success == 0:
                fallback.append(worker)
                continue
            if snapshot.success_rate >= 0.4:
                primary.append(worker)
            else:
                fallback.append(worker)
        if primary:
            return primary
        if fallback:
            return fallback
        raise RuntimeError("No workers available with acceptable success rate")

    def _worker_weight(self, worker: Worker) -> float:
        snapshot = worker.total.snapshot()
        if snapshot.success == 0:
            return self._default_weight
        avg = snapshot.avg_latency_ms
        if avg <= 0:
            return self._default_weight
        return max(0.1, min(10.0, 1000.0 / (avg + 1.0)))

    def ensure_available(self) -> bool:
        return all(worker.classifier.ensure_available() for worker in self.workers)

    async def close(self):
        for worker in self.workers:
            await worker.classifier.close()

    def display_name(self) -> str:
        names = ", ".join(worker.name for worker in self.workers)
        return f"multiplexed[{names}]"

    def is_ai(self) -> bool:
        return any(worker.classifier.is_ai() for worker in self.workers)

    @staticmethod
    def _snapshot_dict(snapshot: MetricSnapshot, ref_time: float) -> dict:
        def _iso(ts):
            if ts is None:
                return None
            return datetime.fromtimestamp(ts).isoformat()

        return {
            "requests": {
                "total": snapshot.requests,
                "success": snapshot.success,
                "failed": snapshot.failure,
                "success_rate": round(snapshot.success_rate * 100, 2),
            },
            "latency": {
                "avg_ms": round(snapshot.avg_latency_ms, 2),
                "total_ms": round(snapshot.latency_ms, 2),
            },
            "first_started": _iso(snapshot.first_started),
            "last_started": _iso(snapshot.last_started or ref_time),
        }

    @staticmethod
    def _combine_snapshots(base: MetricSnapshot, addition: MetricSnapshot) -> MetricSnapshot:
        first = base.first_started
        if addition.first_started is not None and (first is None or addition.first_started < first):
            first = addition.first_started
        last = base.last_started
        if addition.last_started is not None and (last is None or addition.last_started > last):
            last = addition.last_started
        return MetricSnapshot(
            requests=base.requests + addition.requests,
            success=base.success + addition.success,
            failure=base.failure + addition.failure,
            latency_ms=base.latency_ms + addition.latency_ms,
            first_started=first,
            last_started=last,
        )
