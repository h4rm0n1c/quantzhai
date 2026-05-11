import unittest

from proxy.qz_telemetry import RequestTelemetryEmitter, TelemetryBus


class TelemetryBusTests(unittest.TestCase):
    def test_request_telemetry_emitter_injects_request_id(self):
        bus = TelemetryBus(capacity=3)
        emitter = RequestTelemetryEmitter(bus, "req-emitter")

        event = emitter.emit("tool_call_started", {"tool": "web_search"})

        self.assertEqual(event["request_id"], "req-emitter")
        self.assertEqual(event["payload"]["request_id"], "req-emitter")
        self.assertEqual(event["payload"]["tool"], "web_search")

    def test_request_telemetry_emitter_preserves_payload_request_id(self):
        bus = TelemetryBus(capacity=3)
        emitter = RequestTelemetryEmitter(bus, "req-emitter")

        event = emitter.emit("tool_call_started", {"request_id": "req-payload"})

        self.assertEqual(event["request_id"], "req-payload")
        self.assertEqual(event["payload"]["request_id"], "req-payload")

    def test_request_telemetry_emitter_shapes_stream_timing_payload(self):
        bus = TelemetryBus(capacity=3)
        emitter = RequestTelemetryEmitter(bus, "req-stream")

        event = emitter.emit_stream_event_timing(
            "response.output_text.delta",
            received_at=10.0,
            parsed_at=10.125,
            forwarded_at=10.25,
            forwarded_chunks=2,
            forwarded_bytes=42,
            suppressed="",
        )

        payload = event["payload"]
        self.assertEqual(event["type"], "stream_event_timing")
        self.assertEqual(event["request_id"], "req-stream")
        self.assertEqual(payload["event_type"], "response.output_text.delta")
        self.assertEqual(payload["received_to_parsed_ms"], 125.0)
        self.assertEqual(payload["parsed_to_forwarded_ms"], 125.0)
        self.assertEqual(payload["forwarded_chunks"], 2)
        self.assertEqual(payload["forwarded_bytes"], 42)
        self.assertNotIn("suppressed", payload)

    def test_request_telemetry_emitter_stream_timing_records_suppression(self):
        bus = TelemetryBus(capacity=3)
        emitter = RequestTelemetryEmitter(bus, "req-stream")

        event = emitter.emit_stream_event_timing(
            "response.function_call_arguments.delta",
            received_at=10.0,
            parsed_at=10.1,
            forwarded_at=None,
            suppressed="function_call",
        )

        payload = event["payload"]
        self.assertIsNone(payload["parsed_to_forwarded_ms"])
        self.assertEqual(payload["suppressed"], "function_call")

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

    def test_request_events_survive_recent_ring_eviction(self):
        bus = TelemetryBus(capacity=2, request_capacity=3, request_event_capacity=5)

        bus.emit("tool_call_started", {"request_id": "req-1", "tool": "web_search"})
        bus.emit("tool_call_completed", {"request_id": "req-1", "tool": "web_search"})
        bus.emit("sse_event", {"request_id": "req-1", "type": "response.output_text.delta"})
        bus.emit("noise", {"request_id": "req-2"})
        bus.emit("request_completed", {"request_id": "req-1", "status": 200})

        self.assertEqual([event["type"] for event in bus.recent()], ["noise", "request_completed"])
        self.assertEqual(
            [event["type"] for event in bus.request_events("req-1")],
            ["tool_call_started", "tool_call_completed", "request_completed"],
        )

        latest_request = bus.latest_request_summary()
        self.assertEqual(latest_request["latest_completed_request_id"], "req-1")
        self.assertEqual(
            [event["type"] for event in latest_request["latest_completed_events"]],
            ["tool_call_started", "tool_call_completed", "request_completed"],
        )

        payload = bus.request_payload("req-1", limit=2)
        self.assertEqual(payload["schema"], "qz.telemetry.request.v1")
        self.assertEqual([event["type"] for event in payload["events"]], ["tool_call_completed", "request_completed"])

    def test_request_event_index_honors_request_capacity(self):
        bus = TelemetryBus(capacity=10, request_capacity=2, request_event_capacity=5)

        bus.emit("request_started", {"request_id": "req-1"})
        bus.emit("request_started", {"request_id": "req-2"})
        bus.emit("request_started", {"request_id": "req-3"})

        self.assertEqual(bus.request_events("req-1"), [])
        self.assertEqual([event["request_id"] for event in bus.request_events("req-2")], ["req-2"])
        self.assertEqual([event["request_id"] for event in bus.request_events("req-3")], ["req-3"])

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

        latest_request = bus.latest_request_summary()
        self.assertEqual(latest_request["latest_completed_request_id"], "req-1")
        self.assertEqual(latest_request["latest_completed"]["request_id"], "req-1")
        self.assertEqual([event["type"] for event in latest_request["latest_completed_events"]], ["request_completed"])

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

    def test_timing_events_cannot_evict_lifecycle_events(self):
        # Per-request lifecycle capacity=3, timing capacity=2.
        # Flood with stream_event_timing, then check lifecycle events survive.
        bus = TelemetryBus(
            capacity=1000,
            request_capacity=5,
            request_event_capacity=10,
            request_timing_event_capacity=2,
        )
        bus.emit("request_started", {"request_id": "req-1"})
        bus.emit("tool_call_started", {"request_id": "req-1", "tool": "web_search"})
        for i in range(50):
            bus.emit("stream_event_timing", {"request_id": "req-1", "event_type": f"delta_{i}"})
        bus.emit("tool_call_completed", {"request_id": "req-1", "tool": "web_search"})
        bus.emit("request_completed", {"request_id": "req-1", "status": 200})

        events = bus.request_events("req-1")
        types = [e["type"] for e in events]
        lifecycle = [t for t in types if t != "stream_event_timing"]
        timing = [t for t in types if t == "stream_event_timing"]

        # All lifecycle events present despite 50 timing events being emitted
        self.assertIn("request_started", lifecycle)
        self.assertIn("tool_call_started", lifecycle)
        self.assertIn("tool_call_completed", lifecycle)
        self.assertIn("request_completed", lifecycle)
        # Timing is capped at request_timing_event_capacity (2), not 50
        self.assertLessEqual(len(timing), 2)
        # Events are sorted by seq
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs))

    def test_slow_subscriber_drops_oldest_event(self):
        bus = TelemetryBus(capacity=5, subscriber_queue_size=1)

        with bus.subscribe() as events:
            bus.emit("old")
            bus.emit("new")
            received = events.get(timeout=1)

        self.assertEqual(received["type"], "new")


if __name__ == "__main__":
    unittest.main()
