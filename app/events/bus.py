from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
import logging
import time
from typing import TypeVar, cast

from app.observability.errors import classify_error
from app.observability.logging import log_event
from app.observability.metrics import metrics, record_domain_event


EventT = TypeVar("EventT")
EventHandler = Callable[[EventT], None]


class EventBus:
    """Synchronous, in-process event dispatcher."""

    def __init__(self, slow_handler_ms: int = 2000) -> None:
        self._handlers: dict[type[object], list[Callable[[object], None]]] = defaultdict(list)
        self.slow_handler_ms = slow_handler_ms

    def subscribe(
        self,
        event_type: type[EventT],
        handler: EventHandler[EventT],
    ) -> None:
        handlers = self._handlers[event_type]
        typed_handler = cast(Callable[[object], None], handler)
        if typed_handler not in handlers:
            handlers.append(typed_handler)

    def publish(self, event: object) -> None:
        record_domain_event(event)
        for handler in tuple(self._handlers.get(type(event), ())):
            started = time.perf_counter()
            try:
                handler(event)
            except Exception as exc:
                duration = (time.perf_counter() - started) * 1000
                metrics.record_handler(
                    duration, failed=True, slow=duration > self.slow_handler_ms
                )
                log_event(
                    logging.getLogger("x_narrative_tracker.events"), logging.ERROR,
                    "event_handler_failed", "Event handler failed",
                    operation="event_handler", duration_ms=round(duration, 2),
                    error_type=classify_error(exc),
                )
                raise
            duration = (time.perf_counter() - started) * 1000
            slow = duration > self.slow_handler_ms
            metrics.record_handler(duration, failed=False, slow=slow)
            if slow:
                log_event(
                    logging.getLogger("x_narrative_tracker.events"), logging.WARNING,
                    "slow_event_handler", "Slow Event Bus handler",
                    operation="event_handler", duration_ms=round(duration, 2),
                )
