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
        self.assertEqual(recent[0]["schema"], "qz.telemetry.event.v1")
        self.assertIn("monotonic_ts", recent[0])
        self.assertIn("wall_ts", recent[0])
        self.assertEqual(state["event_count"], 2)
        self.assertEqual(state["schema"], "qz.telemetry.state.v1")
        self.assertEqual(state["counters"]["request_started"], 1)
        self.assertEqual(state["latest"]["type"], "request_completed")
        self.assertEqual(state["latest_completed"]["type"], "request_completed")

    def test_emit_promotes_request_id(self):
        bus = TelemetryBus(capacity=3)

        event = bus.emit("request_completed", {"request_id": "req-1"})

        self.assertEqual(event["request_id"], "req-1")

    def test_emit_promotes_nested_request_id(self):
        bus = TelemetryBus(capacity=3)

        runtime_event = bus.emit("request_completed", {"runtime_metrics": {"request_id": "req-runtime"}})
        metadata_event = bus.emit("request_started", {"metadata": {"qz_request_id": "req-meta"}})
        prompt_event = bus.emit("prompt_contract", {"prompt_contract": {"request_id": "req-prompt"}})
        response_event = bus.emit("sse_event", {"response": {"id": "resp-1"}})

        self.assertEqual(runtime_event["request_id"], "req-runtime")
        self.assertEqual(metadata_event["request_id"], "req-meta")
        self.assertEqual(prompt_event["request_id"], "req-prompt")
        self.assertEqual(response_event["request_id"], "resp-1")

    def test_emit_copies_payload_and_handles_unknown_request_id(self):
        bus = TelemetryBus(capacity=3)
        payload = {"nested": {"ok": True}}

        event = bus.emit("request_started", payload)
        payload["request_id"] = "late"

        self.assertEqual(event["request_id"], "")
        self.assertNotIn("request_id", event["payload"])

    def test_sequence_numbers_are_monotonic(self):
        bus = TelemetryBus(capacity=5)

        bus.emit("one")
        bus.emit("two")
        bus.emit("three")

        self.assertEqual([event["seq"] for event in bus.recent()], [1, 2, 3])

    def test_recent_honors_capacity_and_limit(self):
        bus = TelemetryBus(capacity=2)

        bus.emit("one")
        bus.emit("two")
        bus.emit("three")

        self.assertEqual([event["type"] for event in bus.recent()], ["two", "three"])
        self.assertEqual([event["type"] for event in bus.recent(1)], ["three"])

    def test_state_and_recent_payload_include_runtime_truth(self):
        bus = TelemetryBus(capacity=3)
        bus.emit("request_completed", {"request_id": "req-1"})

        unknown_state = bus.state()
        self.assertEqual(unknown_state["runtime"]["schema"], "qz.runtime.summary.v1")
        self.assertEqual(unknown_state["runtime"]["state"], "unknown")
        self.assertEqual(unknown_state["latest_request_id"], "req-1")
        self.assertEqual(unknown_state["latest_completed_request_id"], "req-1")

        runtime = {"schema": "qz.status.summary.v1", "selected_key": "model.gguf", "load_state": "loaded"}
        recent = bus.recent_payload(limit=1, runtime=runtime)

        self.assertEqual(recent["schema"], "qz.telemetry.recent.v1")
        self.assertEqual(recent["state"]["runtime"], runtime)
        self.assertEqual([event["type"] for event in recent["events"]], ["request_completed"])

    def test_stream_open_event_has_schema_and_runtime(self):
        bus = TelemetryBus(capacity=3)
        runtime = {"schema": "qz.status.summary.v1", "selected_key": "model.gguf"}

        event = bus.stream_open_event(runtime=runtime)

        self.assertEqual(event["schema"], "qz.telemetry.stream.v1")
        self.assertEqual(event["type"], "telemetry_stream_open")
        self.assertEqual(event["runtime"], runtime)
        self.assertIn("monotonic_ts", event)

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
