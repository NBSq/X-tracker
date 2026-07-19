from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import TypeVar, cast


EventT = TypeVar("EventT")
EventHandler = Callable[[EventT], None]


class EventBus:
    """Synchronous, in-process event dispatcher."""

    def __init__(self) -> None:
        self._handlers: dict[type[object], list[Callable[[object], None]]] = defaultdict(list)

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
        for handler in tuple(self._handlers.get(type(event), ())):
            handler(event)
