import unittest

from proxy.qz_telemetry import TelemetryBus


class TelemetryBusTests(unittest.TestCase):
    def test_emit_updates_recent_and_state(self):
        bus = TelemetryBus(capacity=3)

        bus.emit("request_started", {"path": "/health"})
        bus.emit("request_completed", {"status": 200})

        recent = bus.recent()
        state = bus.state()

        self.assertEqual([event["type"] for event in recent], ["request_started", "request_completed"])
        self.assertEqual(state["event_count"], 2)
        self.assertEqual(state["counters"]["request_started"], 1)
        self.assertEqual(state["latest"]["type"], "request_completed")

    def test_recent_honors_capacity_and_limit(self):
        bus = TelemetryBus(capacity=2)

        bus.emit("one")
        bus.emit("two")
        bus.emit("three")

        self.assertEqual([event["type"] for event in bus.recent()], ["two", "three"])
        self.assertEqual([event["type"] for event in bus.recent(1)], ["three"])

    def test_subscriber_gets_live_events(self):
        bus = TelemetryBus(capacity=3)

        with bus.subscribe() as events:
            emitted = bus.emit("answer_delta", {"delta": "ok"})
            received = events.get(timeout=1)

        self.assertEqual(received, emitted)

    def test_slow_subscriber_drops_oldest_event(self):
        bus = TelemetryBus(capacity=5, subscriber_queue_size=1)

        with bus.subscribe() as events:
            bus.emit("old")
            bus.emit("new")
            received = events.get(timeout=1)

        self.assertEqual(received["type"], "new")


if __name__ == "__main__":
    unittest.main()
