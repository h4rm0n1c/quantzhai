#!/usr/bin/env python3
from collections import Counter, deque
from contextlib import contextmanager
from queue import Empty, Full, Queue
from threading import Lock
import itertools
import time


TELEMETRY_SCHEMA = "qz.telemetry.event.v1"
TELEMETRY_STATE_SCHEMA = "qz.telemetry.state.v1"
TELEMETRY_RECENT_SCHEMA = "qz.telemetry.recent.v1"
TELEMETRY_STREAM_SCHEMA = "qz.telemetry.stream.v1"
UNKNOWN_RUNTIME_SCHEMA = "qz.runtime.summary.v1"


class TelemetryBus:
    def __init__(self, capacity: int = 1000, subscriber_queue_size: int = 200):
        self.capacity = max(1, int(capacity))
        self.subscriber_queue_size = max(1, int(subscriber_queue_size))
        self._events = deque(maxlen=self.capacity)
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
        }

    def recent_payload(self, limit: int | None = None, runtime: dict | None = None) -> dict:
        return {
            "schema": TELEMETRY_RECENT_SCHEMA,
            "events": self.recent(limit),
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
