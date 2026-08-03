from __future__ import annotations

import logging
import time
from contextlib import ContextDecorator
from typing import Any, Callable

from app.observability.errors import classify_error
from app.observability.logging import log_event
from app.observability.metrics import metrics


logger = logging.getLogger("x_narrative_tracker.observability")


def record_timing(
    operation: str, duration_ms: float, *, threshold_ms: float | None = None,
    fields: dict[str, Any] | None = None,
) -> None:
    slow = threshold_ms is not None and duration_ms > threshold_ms
    metrics.observe(operation, duration_ms, slow=slow)
    if slow:
        log_event(
            logger, logging.WARNING, "slow_operation", "Slow operation detected",
            operation=operation, duration_ms=round(duration_ms, 2), **(fields or {}),
        )


class timed_operation(ContextDecorator):
    def __init__(
        self, operation: str, *, threshold_ms: float | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        self.operation = operation
        self.threshold_ms = threshold_ms
        self.fields = fields or {}
        self.started = 0.0
        self.duration_ms = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.duration_ms = (time.perf_counter() - self.started) * 1000
        record_timing(
            self.operation, self.duration_ms,
            threshold_ms=self.threshold_ms, fields=self.fields,
        )
        if exc is not None:
            error_type = classify_error(exc)
            metrics.record_error(self.operation, error_type)
            if self.operation == "database_query":
                metrics.increment("database_errors_total")
        else:
            metrics.record_success(self.operation)
        return False


def timed(
    operation: str, *, threshold_ms: float | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return timed_operation(operation, threshold_ms=threshold_ms)
