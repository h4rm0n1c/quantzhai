#!/usr/bin/env python3
import json
from dataclasses import dataclass, field
from typing import Any

TURN_METADATA_MAX_BYTES = 100_000


@dataclass
class WorkspaceCandidate:
    repo_root: str
    associated_remote_urls: dict | None = None
    latest_git_commit_hash: str | None = None
    has_changes: bool | None = None


@dataclass
class CodexTurnMetadata:
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    turn_started_at_unix_ms: int | None = None
    sandbox: str | None = None
    workspaces: dict | None = None
    raw: str | None = None


@dataclass
class CodexIdentity:
    client_session_id: str | None = None
    client_thread_id: str | None = None
    client_session_id_source: str | None = None
    client_thread_id_source: str | None = None
    client_request_id: str | None = None
    codex_window_id: str | None = None
    originator: str | None = None
    user_agent: str | None = None
    turn_metadata_raw: str | None = None
    turn_metadata: CodexTurnMetadata | None = None
    turn_id: str | None = None
    turn_started_at_unix_ms: int | None = None
    workspace_candidates: list[WorkspaceCandidate] | None = None
    identity_conflict: bool = False
    conflict_notes: list[str] | None = None


def header_lookup(headers_raw: dict, name: str) -> str | None:
    if not isinstance(headers_raw, dict) or not name:
        return None
    if name in headers_raw:
        value = headers_raw[name]
        if isinstance(value, str):
            return value
    name_lower = name.lower()
    for k, v in headers_raw.items():
        if isinstance(k, str) and k.lower() == name_lower and isinstance(v, str):
            return v
    return None


def _normalize_remote_url(url: str) -> str:
    if not isinstance(url, str):
        return ""
    url = url.strip()
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url


def parse_codex_turn_metadata_header(value: str | None) -> dict | None:
    if not value or not isinstance(value, str):
        return None
    if len(value.encode("utf-8")) > TURN_METADATA_MAX_BYTES:
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def extract_workspace_candidates(parsed_turn_metadata: dict | None) -> list[WorkspaceCandidate]:
    candidates: list[WorkspaceCandidate] = []
    if not isinstance(parsed_turn_metadata, dict):
        return candidates
    workspaces = parsed_turn_metadata.get("workspaces")
    if not isinstance(workspaces, dict):
        return candidates
    for repo_root, ws_data in workspaces.items():
        if not isinstance(repo_root, str) or not repo_root:
            continue
        if not isinstance(ws_data, dict):
            ws_data = {}
        remotes = ws_data.get("associated_remote_urls")
        normalized_remotes = None
        if isinstance(remotes, dict):
            normalized_remotes = {}
            for remote_name, url in remotes.items():
                if isinstance(url, str):
                    normalized_remotes[remote_name] = _normalize_remote_url(url)
                elif url is not None:
                    normalized_remotes[remote_name] = url
        commit_hash = ws_data.get("latest_git_commit_hash")
        if not isinstance(commit_hash, str):
            commit_hash = None
        has_changes = ws_data.get("has_changes")
        if has_changes is not None and not isinstance(has_changes, bool):
            has_changes = None
        candidates.append(WorkspaceCandidate(
            repo_root=repo_root,
            associated_remote_urls=normalized_remotes,
            latest_git_commit_hash=commit_hash,
            has_changes=has_changes,
        ))
    return candidates


def extract_codex_identity(headers_raw: dict) -> CodexIdentity:
    client_session_id: str | None = None
    client_session_id_source: str | None = None
    for name in ("session_id", "session-id"):
        val = header_lookup(headers_raw, name)
        if val is not None:
            client_session_id = val
            client_session_id_source = name
            break

    client_thread_id: str | None = None
    client_thread_id_source: str | None = None
    for name in ("thread_id", "thread-id"):
        val = header_lookup(headers_raw, name)
        if val is not None:
            client_thread_id = val
            client_thread_id_source = name
            break

    client_request_id = header_lookup(headers_raw, "x-client-request-id") or None
    codex_window_id = header_lookup(headers_raw, "x-codex-window-id") or None
    originator = header_lookup(headers_raw, "originator") or None
    user_agent = header_lookup(headers_raw, "user-agent") or None
    turn_metadata_raw = header_lookup(headers_raw, "x-codex-turn-metadata") or None
    parsed_tm = parse_codex_turn_metadata_header(turn_metadata_raw)

    turn_metadata: CodexTurnMetadata | None = None
    turn_id: str | None = None
    turn_started_at_unix_ms: int | None = None
    conflict_notes: list[str] = []
    identity_conflict = False

    if parsed_tm is not None:
        tm_session_id = parsed_tm.get("session_id")
        tm_thread_id = parsed_tm.get("thread_id")
        raw_turn_id = parsed_tm.get("turn_id")
        turn_id = raw_turn_id if isinstance(raw_turn_id, str) else None
        ts_val = parsed_tm.get("turn_started_at_unix_ms")
        turn_started_at_unix_ms = int(ts_val) if isinstance(ts_val, (int, float)) else None

        if client_session_id and isinstance(tm_session_id, str):
            if client_session_id != tm_session_id:
                identity_conflict = True
                conflict_notes.append(
                    f"session_id header ({client_session_id}) differs from "
                    f"turn_metadata.session_id ({tm_session_id})"
                )

        if not client_thread_id and isinstance(tm_thread_id, str):
            client_thread_id = tm_thread_id
            client_thread_id_source = "turn_metadata"
        elif client_thread_id and isinstance(tm_thread_id, str) and client_thread_id != tm_thread_id:
            identity_conflict = True
            conflict_notes.append(
                f"thread_id header ({client_thread_id}) differs from "
                f"turn_metadata.thread_id ({tm_thread_id})"
            )

        turn_metadata = CodexTurnMetadata(
            session_id=tm_session_id if isinstance(tm_session_id, str) else None,
            thread_id=tm_thread_id if isinstance(tm_thread_id, str) else None,
            turn_id=turn_id,
            turn_started_at_unix_ms=turn_started_at_unix_ms,
            sandbox=parsed_tm.get("sandbox") if isinstance(parsed_tm.get("sandbox"), str) else None,
            workspaces=parsed_tm.get("workspaces") if isinstance(parsed_tm.get("workspaces"), dict) else None,
            raw=turn_metadata_raw,
        )

    workspace_candidates = extract_workspace_candidates(parsed_tm)

    return CodexIdentity(
        client_session_id=client_session_id or None,
        client_thread_id=client_thread_id or None,
        client_session_id_source=client_session_id_source,
        client_thread_id_source=client_thread_id_source,
        client_request_id=client_request_id,
        codex_window_id=codex_window_id,
        originator=originator,
        user_agent=user_agent,
        turn_metadata_raw=turn_metadata_raw,
        turn_metadata=turn_metadata,
        turn_id=turn_id,
        turn_started_at_unix_ms=turn_started_at_unix_ms,
        workspace_candidates=workspace_candidates if workspace_candidates else None,
        identity_conflict=identity_conflict,
        conflict_notes=conflict_notes if conflict_notes else None,
    )
