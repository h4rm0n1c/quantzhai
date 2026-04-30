#!/usr/bin/env python3
from collections import Counter, deque
from contextlib import contextmanager
from queue import Empty, Full, Queue
from threading import Lock
import itertools
import time


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
        now = time.time()
        event = {
            "seq": next(self._seq),
            "ts": now,
            "type": str(event_type or "event"),
            "payload": payload if isinstance(payload, dict) else {},
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

    def state(self) -> dict:
        now = time.time()
        with self._lock:
            latest = self._events[-1] if self._events else None
            latest_completed = self._latest_completed
            latest_throughput = self._latest_throughput
            event_count = len(self._events)
            counters = dict(self._counters)

        return {
            "status": "ok",
            "started_at": self.started_at,
            "now": now,
            "uptime_seconds": max(0.0, now - self.started_at),
            "event_count": event_count,
            "capacity": self.capacity,
            "counters": counters,
            "latest": latest,
            "latest_completed": latest_completed,
            "latest_throughput": latest_throughput,
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


DEFAULT_TELEMETRY = TelemetryBus()
