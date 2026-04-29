#!/usr/bin/env python3
import json
import urllib.error
import urllib.request
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class BackendResponse:
    status: int
    content_type: str
    data: bytes


class BackendClient:
    def __init__(self, upstream: str, authorization: str = "Bearer local"):
        self.upstream = upstream.rstrip("/")
        self.authorization = authorization or "Bearer local"

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return self.upstream + path_or_url

    def request(
        self,
        path_or_url: str,
        method: str = "GET",
        body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 900,
    ) -> BackendResponse:
        req_headers = {
            "Authorization": self.authorization,
        }
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(
            self._resolve_url(path_or_url),
            data=body,
            method=method,
            headers=req_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return BackendResponse(
                    status=resp.status,
                    content_type=resp.headers.get("Content-Type", "application/json"),
                    data=resp.read(),
                )
        except urllib.error.HTTPError as e:
            return BackendResponse(
                status=e.code,
                content_type=e.headers.get("Content-Type", "application/json"),
                data=e.read(),
            )

    def get_json(self, path: str, timeout: float = 30) -> Tuple[int, Dict[str, Any]]:
        resp = self.request(path, method="GET", headers={"Accept": "application/json"}, timeout=timeout)
        try:
            payload = json.loads(resp.data.decode("utf-8"))
        except Exception:
            payload = {}
        return resp.status, payload

    def post_json(self, path: str, body: Dict[str, Any], timeout: float = 900) -> BackendResponse:
        data = json.dumps(body).encode("utf-8")
        return self.request(
            path,
            method="POST",
            body=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    def get_models(self, timeout: float = 30) -> Dict[str, Any]:
        _, payload = self.get_json("/models", timeout=timeout)
        return payload

    def get_health(self, timeout: float = 10) -> BackendResponse:
        return self.request("/health", method="GET", headers={"Accept": "application/json"}, timeout=timeout)

    def get_model_entry(self, model_id: str, timeout: float = 30) -> Dict[str, Any]:
        payload = self.get_models(timeout=timeout)
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") == model_id or item.get("name") == model_id:
                return item
        return {}

    def wait_for_model_ready(self, model_id: str, timeout: float = 120, poll_interval: float = 0.5) -> Tuple[bool, Dict[str, Any]]:
        deadline = time.time() + max(0.0, float(timeout))
        poll_interval = max(0.1, float(poll_interval))
        last_health = self.get_health(timeout=min(10.0, timeout))
        last_entry: Dict[str, Any] = {}

        while time.time() <= deadline:
            last_entry = self.get_model_entry(model_id, timeout=min(10.0, max(0.1, deadline - time.time())))
            status = last_entry.get("status") or {}
            state = status.get("value") if isinstance(status, dict) else None
            if last_health.status == 200 and state == "loaded":
                return True, {
                    "health_status": last_health.status,
                    "health_body": json.loads(last_health.data.decode("utf-8")) if last_health.data else {},
                    "model_entry": last_entry,
                }
            time.sleep(min(poll_interval, max(0.1, deadline - time.time())))
            last_health = self.get_health(timeout=min(10.0, max(0.1, deadline - time.time())))

        return False, {
            "health_status": last_health.status,
            "health_body": json.loads(last_health.data.decode("utf-8")) if last_health.data else {},
            "model_entry": last_entry,
        }

    def load_model(self, model_id: str, timeout: float = 120) -> BackendResponse:
        return self.post_json("/models/load", {"model": model_id}, timeout=timeout)
