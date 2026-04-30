#!/usr/bin/env python3
import json
import os
import shlex
import subprocess
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
        return self.wait_for_model_state(model_id, {"loaded"}, timeout=timeout, poll_interval=poll_interval)

    def wait_for_model_state(self, model_id: str, states, timeout: float = 120, poll_interval: float = 0.5) -> Tuple[bool, Dict[str, Any]]:
        wanted = {str(state) for state in (states or []) if str(state)}
        deadline = time.time() + max(0.0, float(timeout))
        poll_interval = max(0.1, float(poll_interval))
        last_health = self.get_health(timeout=min(10.0, timeout))
        last_entry: Dict[str, Any] = {}

        while time.time() <= deadline:
            last_entry = self.get_model_entry(model_id, timeout=min(10.0, max(0.1, deadline - time.time())))
            status = last_entry.get("status") or {}
            state = status.get("value") if isinstance(status, dict) else None
            if state in wanted:
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

    def unload_model(self, model_id: str, timeout: float = 120) -> BackendResponse:
        return self.post_json("/models/unload", {"model": model_id}, timeout=timeout)

    def _docker_command(self) -> list[str]:
        raw = os.environ.get("QZ_DOCKER_CMD", "docker")
        return shlex.split(raw)

    def _backend_launch_args(self, context_size: int) -> list[str]:
        container = os.environ.get("QZ_CONTAINER", "qwen36turbo")
        image = os.environ.get("QZ_IMAGE", "thetom-llama-cpp-turboquant:cuda-server")
        model_dir = os.environ.get("QZ_MODEL_DIR") or os.path.join(os.environ.get("QZ_ROOT", ""), "var", "models")
        server_port = os.environ.get("QZ_SERVER_PORT", "18084")
        parallel = os.environ.get("QZ_PARALLEL", "1")
        batch = os.environ.get("QZ_BATCH", "4096")
        ubatch = os.environ.get("QZ_UBATCH", "512")
        threads = os.environ.get("QZ_THREADS", "12")
        thread_batch = os.environ.get("QZ_THREAD_BATCH", "12")
        tensor_split = os.environ.get("QZ_TENSOR_SPLIT", "9,17")
        main_gpu = os.environ.get("QZ_MAIN_GPU", "0")
        cache_ram = os.environ.get("QZ_CACHE_RAM", "8192")
        cache_reuse = os.environ.get("QZ_CACHE_REUSE", "256")
        kv_key = os.environ.get("QZ_KV_KEY", "q8_0")
        kv_value = os.environ.get("QZ_KV_VALUE", "turbo3")

        return [
            "run",
            "-d",
            "--name",
            container,
            "--gpus",
            "all",
            "--cap-add",
            "IPC_LOCK",
            "--ulimit",
            "memlock=-1:-1",
            "-p",
            f"{server_port}:8080",
            "--mount",
            f"type=bind,src={model_dir},dst=/models,readonly",
            image,
            "--models-dir",
            "/models",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "-ngl",
            "999",
            "-c",
            str(int(context_size)),
            "-np",
            parallel,
            "-b",
            batch,
            "-ub",
            ubatch,
            "-t",
            threads,
            "-tb",
            thread_batch,
            "-fa",
            "on",
            "--split-mode",
            "layer",
            "--tensor-split",
            tensor_split,
            "--main-gpu",
            main_gpu,
            "--kv-unified",
            "--reasoning",
            "on",
            "--reasoning-budget",
            "-1",
            "--cache-ram",
            cache_ram,
            "--cache-reuse",
            cache_reuse,
            "--mlock",
            "-ctk",
            kv_key,
            "-ctv",
            kv_value,
            "--metrics",
            "--reasoning-format",
            "deepseek",
        ]

    def _docker_logs(self, container: str, tail: int = 160) -> str:
        cmd = self._docker_command() + ["logs", "--tail", str(int(tail)), container]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        output = (proc.stdout or "") + (proc.stderr or "")
        return output.strip()

    def restart_container(self, context_size: int, timeout: float = 120, health_timeout: float | None = None) -> Dict[str, Any]:
        timeout = max(1.0, float(timeout))
        health_timeout = timeout if health_timeout is None else max(1.0, float(health_timeout))
        container = os.environ.get("QZ_CONTAINER", "qwen36turbo")
        docker = self._docker_command()
        model_dir = os.environ.get("QZ_MODEL_DIR") or os.path.join(os.environ.get("QZ_ROOT", ""), "var", "models")
        if model_dir:
            os.makedirs(model_dir, exist_ok=True)

        rm_cmd = docker + ["rm", "-f", container]
        subprocess.run(rm_cmd, check=False, capture_output=True, text=True)

        run_cmd = docker + self._backend_launch_args(context_size)
        run_proc = subprocess.run(run_cmd, check=False, capture_output=True, text=True)
        if run_proc.returncode != 0:
            stderr = (run_proc.stderr or "").strip()
            stdout = (run_proc.stdout or "").strip()
            detail = "; ".join(part for part in (stderr, stdout) if part)
            if not detail:
                detail = f"docker run exited {run_proc.returncode}"
            raise RuntimeError(f"backend restart failed: {detail}")

        deadline = time.time() + health_timeout
        last_health = None
        while time.time() < deadline:
            last_health = self.get_health(timeout=min(10.0, max(0.1, deadline - time.time())))
            if last_health.status == 200:
                return {
                    "container": container,
                    "context_length": int(context_size),
                    "health_status": last_health.status,
                    "health_body": json.loads(last_health.data.decode("utf-8")) if last_health.data else {},
                    "stdout": (run_proc.stdout or "").strip(),
                }
            time.sleep(min(1.0, max(0.1, deadline - time.time())))

        logs = self._docker_logs(container)
        raise RuntimeError(
            f"backend restart timed out waiting for health after launching context {int(context_size)}; "
            f"health_status={(last_health.status if last_health else 'unknown')}; logs={logs}"
        )
