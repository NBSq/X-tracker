import unittest
from dataclasses import dataclass

from app.events.bus import EventBus


@dataclass(frozen=True)
class ExampleEvent:
    value: int


@dataclass(frozen=True)
class OtherEvent:
    value: str


class EventBusTests(unittest.TestCase):
    def test_publish_calls_subscribers_in_registration_order(self) -> None:
        bus = EventBus()
        received = []
        bus.subscribe(ExampleEvent, lambda event: received.append(("first", event.value)))
        bus.subscribe(ExampleEvent, lambda event: received.append(("second", event.value)))

        bus.publish(ExampleEvent(7))

        self.assertEqual(received, [("first", 7), ("second", 7)])

    def test_publish_only_calls_matching_event_type(self) -> None:
        bus = EventBus()
        received = []
        bus.subscribe(ExampleEvent, lambda event: received.append(event.value))

        bus.publish(OtherEvent("ignored"))

        self.assertEqual(received, [])

    def test_duplicate_handler_is_registered_once(self) -> None:
        bus = EventBus()
        received = []

        def handler(event: ExampleEvent) -> None:
            received.append(event.value)

        bus.subscribe(ExampleEvent, handler)
        bus.subscribe(ExampleEvent, handler)
        bus.publish(ExampleEvent(3))

        self.assertEqual(received, [3])

    def test_nested_publication_is_supported(self) -> None:
        bus = EventBus()
        received = []
        bus.subscribe(
            ExampleEvent,
            lambda event: bus.publish(OtherEvent(str(event.value))),
        )
        bus.subscribe(OtherEvent, lambda event: received.append(event.value))

        bus.publish(ExampleEvent(9))

        self.assertEqual(received, ["9"])


if __name__ == "__main__":
    unittest.main()
