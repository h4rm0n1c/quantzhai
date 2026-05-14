#!/usr/bin/env python3
from collections import Counter, OrderedDict, deque
from contextlib import contextmanager
from queue import Empty, Full, Queue
from threading import Lock
import itertools
import time


TELEMETRY_SCHEMA = "qz.telemetry.event.v1"
TELEMETRY_STATE_SCHEMA = "qz.telemetry.state.v1"
TELEMETRY_RECENT_SCHEMA = "qz.telemetry.recent.v1"
TELEMETRY_REQUEST_SCHEMA = "qz.telemetry.request.v1"
TELEMETRY_STREAM_SCHEMA = "qz.telemetry.stream.v1"
UNKNOWN_RUNTIME_SCHEMA = "qz.runtime.summary.v1"

REQUEST_LIFECYCLE_EVENT_TYPES = {
    "client_disconnected",
    "empty_answer_repair_completed",
    "empty_answer_repair_failed",
    "empty_answer_repair_started",
    "prompt_contract",
    "private_tool_call_aborted",
    "reasoning_only_completed_without_answer",
    "reasoning_only_aborted",
    "request_admitted",
    "request_completed",
    "request_failed",
    "request_queued",
    "request_started",
    "stream_completed",
    "throughput_sample",
    "tool_call_completed",
    "tool_call_failed",
    "tool_call_started",
    "tool_connection_failed",
    "tool_escalation_requested",
    "tool_sandbox_denied",
    "repeated_read_signal",
    "responses_rejected_backend_unavailable",
    "responses_rejected_model_missing",
    "responses_rejected_proxy_not_ready",
}

REQUEST_TIMING_EVENT_TYPES = {
    "stream_event_timing",
}

REQUEST_RETAINED_EVENT_TYPES = REQUEST_LIFECYCLE_EVENT_TYPES | REQUEST_TIMING_EVENT_TYPES


class RequestTelemetryEmitter:
    """Request-scoped telemetry helper that owns common payload shaping."""

    def __init__(self, telemetry=None, request_id: str = ""):
        self.telemetry = telemetry
        self.request_id = request_id or ""

    def emit(self, event_type: str, payload: dict | None = None):
        if not self.telemetry:
            return None
        try:
            event_payload = dict(payload) if isinstance(payload, dict) else {}
            if self.request_id and not event_payload.get("request_id"):
                event_payload["request_id"] = self.request_id
            return self.telemetry.emit(event_type, event_payload)
        except Exception:
            return None

    def emit_stream_event_timing(
        self,
        event_type: str,
        received_at: float,
        parsed_at: float,
        forwarded_at: float | None,
        *,
        forwarded_chunks: int = 0,
        forwarded_bytes: int = 0,
        suppressed: str = "",
    ):
        emitted_at = time.time()
        payload = {
            "event_type": event_type or "event",
            "received_to_parsed_ms": round(max(0.0, parsed_at - received_at) * 1000.0, 3),
            "parsed_to_forwarded_ms": None,
            "received_to_telemetry_ms": round(max(0.0, emitted_at - received_at) * 1000.0, 3),
            "forwarded_chunks": int(forwarded_chunks),
            "forwarded_bytes": int(forwarded_bytes),
        }
        if forwarded_at is not None:
            payload["parsed_to_forwarded_ms"] = round(max(0.0, forwarded_at - parsed_at) * 1000.0, 3)
        if suppressed:
            payload["suppressed"] = suppressed
        return self.emit("stream_event_timing", payload)


class TelemetryBus:
    def __init__(
        self,
        capacity: int = 1000,
        subscriber_queue_size: int = 200,
        request_capacity: int = 50,
        request_event_capacity: int = 200,
        request_timing_event_capacity: int = 150,
    ):
        self.capacity = max(1, int(capacity))
        self.subscriber_queue_size = max(1, int(subscriber_queue_size))
        self.request_capacity = max(1, int(request_capacity))
        self.request_event_capacity = max(1, int(request_event_capacity))
        self.request_timing_event_capacity = max(1, int(request_timing_event_capacity))
        self._events = deque(maxlen=self.capacity)
        self._request_events = OrderedDict()
        self._counters = Counter()
        self._subscribers = set()
        self._seq = itertools.count(1)
        self._lock = Lock()
        self.started_at = time.time()
        self._latest_completed = None
        self._latest_throughput = None

    def emit(self, event_type: str, payload: dict | None = None) -> dict:
        now_wall = time.time()
        now_mono = time.monotonic()
        payload = dict(payload) if isinstance(payload, dict) else {}
        event = {
            "schema": TELEMETRY_SCHEMA,
            "seq": next(self._seq),
            "ts": now_wall,
            "wall_ts": now_wall,
            "monotonic_ts": now_mono,
            "type": str(event_type or "event"),
            "request_id": self._request_id_from_payload(payload),
            "payload": payload,
        }

        with self._lock:
            self._events.append(event)
            self._append_request_event(event)
            self._counters[event["type"]] += 1
            if event["type"] == "request_completed":
                self._latest_completed = event
            if event["type"] == "throughput_sample":
                self._latest_throughput = event
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            self._publish_to_subscriber(subscriber, event)

        return event

    def recent(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            events = list(self._events)

        if limit is None:
            return events

        try:
            limit = int(limit)
        except Exception:
            limit = len(events)
        limit = max(0, limit)
        if limit == 0:
            return []
        return events[-limit:]

    def state(self, runtime: dict | None = None) -> dict:
        now_wall = time.time()
        now_mono = time.monotonic()
        with self._lock:
            latest = self._events[-1] if self._events else None
            latest_completed = self._latest_completed
            latest_throughput = self._latest_throughput
            latest_completed_events = self._request_events_for_locked(self._event_request_id(latest_completed))
            event_count = len(self._events)
            counters = dict(self._counters)

        return {
            "schema": TELEMETRY_STATE_SCHEMA,
            "status": "ok",
            "started_at": self.started_at,
            "now": now_wall,
            "wall_ts": now_wall,
            "monotonic_ts": now_mono,
            "uptime_seconds": max(0.0, now_wall - self.started_at),
            "event_count": event_count,
            "capacity": self.capacity,
            "counters": counters,
            "runtime": self._runtime_payload(runtime),
            "latest_request_id": self._event_request_id(latest),
            "latest_completed_request_id": self._event_request_id(latest_completed),
            "latest": latest,
            "latest_completed": latest_completed,
            "latest_throughput": latest_throughput,
            "latest_completed_events": latest_completed_events,
        }

    def recent_payload(self, limit: int | None = None, runtime: dict | None = None) -> dict:
        return {
            "schema": TELEMETRY_RECENT_SCHEMA,
            "events": self.recent(limit),
            "state": self.state(runtime=runtime),
        }

    def latest_request_summary(self) -> dict:
        with self._lock:
            latest_completed = self._latest_completed
            latest_throughput = self._latest_throughput
            request_id = self._event_request_id(latest_completed)
            latest_completed_events = self._request_events_for_locked(request_id)
        return {
            "latest_completed_request_id": request_id,
            "latest_completed": latest_completed,
            "latest_throughput": latest_throughput,
            "latest_completed_events": latest_completed_events,
        }

    def request_events(self, request_id: str, limit: int | None = None) -> list[dict]:
        request_id = str(request_id or "")
        with self._lock:
            events = self._request_events_for_locked(request_id)
        if limit is None:
            return events
        try:
            limit = int(limit)
        except Exception:
            limit = len(events)
        limit = max(0, limit)
        if limit == 0:
            return []
        return events[-limit:]

    def request_payload(self, request_id: str, limit: int | None = None, runtime: dict | None = None) -> dict:
        request_id = str(request_id or "")
        return {
            "schema": TELEMETRY_REQUEST_SCHEMA,
            "request_id": request_id,
            "events": self.request_events(request_id, limit=limit),
            "state": self.state(runtime=runtime),
        }

    def stream_open_event(self, runtime: dict | None = None) -> dict:
        now_wall = time.time()
        return {
            "schema": TELEMETRY_STREAM_SCHEMA,
            "type": "telemetry_stream_open",
            "ts": now_wall,
            "wall_ts": now_wall,
            "monotonic_ts": time.monotonic(),
            "request_id": "",
            "runtime": self._runtime_payload(runtime),
        }

    @contextmanager
    def subscribe(self):
        queue = Queue(maxsize=self.subscriber_queue_size)
        with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            with self._lock:
                self._subscribers.discard(queue)

    def _publish_to_subscriber(self, subscriber: Queue, event: dict):
        try:
            subscriber.put_nowait(event)
            return
        except Full:
            pass

        try:
            subscriber.get_nowait()
        except Empty:
            pass

        try:
            subscriber.put_nowait(event)
        except Full:
            pass

    def _append_request_event(self, event: dict):
        request_id = event.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return
        event_type = event.get("type")
        if event_type not in REQUEST_RETAINED_EVENT_TYPES:
            return

        bucket = self._request_events.get(request_id)
        if bucket is None:
            bucket = {
                "lifecycle": deque(maxlen=self.request_event_capacity),
                "timing": deque(maxlen=self.request_timing_event_capacity),
            }
            self._request_events[request_id] = bucket
        else:
            self._request_events.move_to_end(request_id)

        if event_type in REQUEST_TIMING_EVENT_TYPES:
            bucket["timing"].append(event)
        else:
            bucket["lifecycle"].append(event)

        while len(self._request_events) > self.request_capacity:
            self._request_events.popitem(last=False)

    def _request_events_for_locked(self, request_id: str) -> list[dict]:
        if not request_id:
            return []
        bucket = self._request_events.get(request_id)
        if not bucket:
            return []
        merged = list(bucket["lifecycle"]) + list(bucket["timing"])
        merged.sort(key=lambda e: e.get("seq", 0))
        return merged

    @staticmethod
    def _request_id_from_payload(payload: dict) -> str:
        for key in ("request_id", "qz_request_id", "response_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            for key in ("qz_request_id", "request_id"):
                value = metadata.get(key)
                if isinstance(value, str) and value:
                    return value
        runtime = payload.get("runtime_metrics")
        if isinstance(runtime, dict):
            value = runtime.get("request_id")
            if isinstance(value, str) and value:
                return value
        prompt_contract = payload.get("prompt_contract")
        if isinstance(prompt_contract, dict):
            value = prompt_contract.get("request_id")
            if isinstance(value, str) and value:
                return value
        response = payload.get("response")
        if isinstance(response, dict):
            value = response.get("id")
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _event_request_id(event: dict | None) -> str:
        if isinstance(event, dict) and isinstance(event.get("request_id"), str):
            return event.get("request_id") or ""
        return ""

    @staticmethod
    def _runtime_payload(runtime: dict | None) -> dict:
        if isinstance(runtime, dict) and runtime:
            return dict(runtime)
        return {
            "schema": UNKNOWN_RUNTIME_SCHEMA,
            "state": "unknown",
        }


DEFAULT_TELEMETRY = TelemetryBus()
