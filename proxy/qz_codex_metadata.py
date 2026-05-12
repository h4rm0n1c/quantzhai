#!/usr/bin/env python3
import json
from dataclasses import dataclass

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
class CodexRequestMetadata:
    previous_response_id: str | None = None
    prompt_cache_key: str | None = None
    service_tier: str | None = None
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    text_verbosity: str | None = None
    parallel_tool_calls: bool | None = None
    tool_choice: str | dict | None = None
    store: bool | None = None
    stream: bool | None = None
    include: list | None = None
    client_metadata: dict | None = None
    client_installation_id_from_body: str | None = None
    has_output_schema: bool = False
    tools_count: int = 0
    tool_names: list[str] | None = None


@dataclass
class CodexIdentity:
    client_session_id: str | None = None
    client_thread_id: str | None = None
    client_session_id_source: str | None = None
    client_thread_id_source: str | None = None
    client_request_id: str | None = None
    codex_window_id: str | None = None
    codex_window_thread_id: str | None = None
    codex_window_generation: int | None = None
    codex_window_parse_error: bool = False
    originator: str | None = None
    user_agent: str | None = None
    turn_metadata_raw: str | None = None
    turn_metadata: CodexTurnMetadata | None = None
    turn_id: str | None = None
    turn_started_at_unix_ms: int | None = None
    workspace_candidates: list[WorkspaceCandidate] | None = None
    workspace_id: str | None = None
    workspace_id_source: str | None = None
    codex_turn_state_raw: str | None = None
    installation_id: str | None = None
    parent_thread_id: str | None = None
    subagent: str | None = None
    memgen_raw: str | None = None
    memgen_parse_error: bool = False
    is_memgen: bool = False
    identity_conflict: bool = False
    conflict_notes: list[str] | None = None


@dataclass
class CodexRequestContext:
    identity: CodexIdentity
    body_metadata: CodexRequestMetadata | None = None
    qz_session_id: str | None = None
    memory_domain: str = "isolated"


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


def parse_codex_window_id(value: str | None) -> tuple[str | None, int | None]:
    """
    Parses a Codex window ID which is typically {thread_id}:{window_generation}.
    Uses rsplit to handle thread IDs that might contain colons.
    """
    if not value or not isinstance(value, str):
        return None, None

    if ":" not in value:
        return None, None

    thread_id, gen_str = value.rsplit(":", 1)
    if not thread_id:
        return None, None

    try:
        generation = int(gen_str)
        if generation < 0:
            return None, None
        return thread_id, generation
    except (ValueError, TypeError):
        return None, None


def parse_memgen_header(value: str | None) -> tuple[bool, bool]:
    if value is None:
        return False, False
    if not isinstance(value, str):
        return False, True

    normalized = value.lower().strip()
    if not normalized:
        return False, False
    if normalized in ("true", "1", "yes"):
        return True, False
    if normalized in ("false", "0", "no"):
        return False, False
    return False, True


def _header_values(headers_raw: dict, names: tuple[str, ...]) -> list[tuple[str, str]]:
    if not isinstance(headers_raw, dict):
        return []

    wanted = {name.lower() for name in names}
    seen_keys: set[str] = set()
    values: list[tuple[str, str]] = []

    # Prefer exact names first so source labels remain stable in normal captures.
    for name in names:
        if name in headers_raw and isinstance(headers_raw[name], str):
            values.append((name, headers_raw[name]))
            seen_keys.add(name)

    for key, value in headers_raw.items():
        if key in seen_keys:
            continue
        if isinstance(key, str) and key.lower() in wanted and isinstance(value, str):
            values.append((key, value))
            seen_keys.add(key)

    return values


def _choose_header_identity(
    headers_raw: dict,
    names: tuple[str, ...],
    label: str,
    conflict_notes: list[str],
) -> tuple[str | None, str | None, bool]:
    values = _header_values(headers_raw, names)
    if not values:
        return None, None, False

    first_source, first_value = values[0]
    distinct_values = {value for _, value in values}
    if len(distinct_values) > 1:
        conflict_notes.append(
            f"{label} header variants disagree: "
            + ", ".join(f"{source}={value}" for source, value in values)
        )
        return first_value, first_source, True

    return first_value, first_source, False


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
                if not isinstance(remote_name, str):
                    continue
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


def resolve_workspace_id(candidates: list[WorkspaceCandidate] | None) -> tuple[str, str]:
    """
    Resolves a stable workspace_id from Codex turn metadata candidates.
    Logic from codex-context-memory-contract.md.
    """
    if not candidates:
        return "unknown", "unknown"

    # Rule: If any workspace candidate has normalized remote url, use it.
    # Prefer 'origin' if available.
    for cand in candidates:
        if not cand.associated_remote_urls:
            continue
        url = cand.associated_remote_urls.get("origin")
        if not url:
            # Pick any available remote URL
            for remote_name, remote_url in cand.associated_remote_urls.items():
                if remote_url:
                    url = remote_url
                    break
        if url:
            return f"remote:{url}", "codex_turn_metadata_remote"

    # Rule: Else if any workspace candidate has repo_root, use its sha256 hash.
    for cand in candidates:
        if cand.repo_root:
            import hashlib
            # Normalize: strip trailing slash and lowercase for stable hashing on some OSs,
            # though repo_root from Codex is usually already normalized.
            root = cand.repo_root.rstrip("/")
            root_hash = hashlib.sha256(root.encode("utf-8")).hexdigest()
            return f"path:{root_hash}", "codex_turn_metadata_repo_root"

    return "unknown", "unknown"


def extract_codex_identity(headers_raw: dict) -> CodexIdentity:
    conflict_notes: list[str] = []

    client_session_id, client_session_id_source, session_variant_conflict = _choose_header_identity(
        headers_raw,
        ("session_id", "session-id"),
        "session_id",
        conflict_notes,
    )
    client_thread_id, client_thread_id_source, thread_variant_conflict = _choose_header_identity(
        headers_raw,
        ("thread_id", "thread-id"),
        "thread_id",
        conflict_notes,
    )

    client_request_id = header_lookup(headers_raw, "x-client-request-id")
    codex_window_id = header_lookup(headers_raw, "x-codex-window-id")
    originator = header_lookup(headers_raw, "originator")
    user_agent = header_lookup(headers_raw, "user-agent")
    turn_metadata_raw = header_lookup(headers_raw, "x-codex-turn-metadata")
    parsed_tm = parse_codex_turn_metadata_header(turn_metadata_raw)

    codex_turn_state_raw = header_lookup(headers_raw, "x-codex-turn-state")
    installation_id = header_lookup(headers_raw, "x-codex-installation-id")
    parent_thread_id = header_lookup(headers_raw, "x-codex-parent-thread-id")
    subagent = header_lookup(headers_raw, "x-openai-subagent")
    memgen_raw = header_lookup(headers_raw, "x-openai-memgen-request")
    is_memgen, memgen_parse_error = parse_memgen_header(memgen_raw)
    if memgen_parse_error:
        conflict_notes.append(f"Unrecognised x-openai-memgen-request value: {memgen_raw}")

    win_thread_id, win_gen = parse_codex_window_id(codex_window_id)
    codex_window_parse_error = False
    if codex_window_id and (win_thread_id is None or win_gen is None):
        codex_window_parse_error = True
        conflict_notes.append(f"Malformed x-codex-window-id: {codex_window_id}")

    turn_metadata: CodexTurnMetadata | None = None
    turn_id: str | None = None
    turn_started_at_unix_ms: int | None = None
    identity_conflict = session_variant_conflict or thread_variant_conflict

    if parsed_tm is not None:
        tm_session_id = parsed_tm.get("session_id")
        tm_thread_id = parsed_tm.get("thread_id")
        raw_turn_id = parsed_tm.get("turn_id")
        turn_id = raw_turn_id if isinstance(raw_turn_id, str) else None
        ts_val = parsed_tm.get("turn_started_at_unix_ms")
        if isinstance(ts_val, int) and not isinstance(ts_val, bool):
            turn_started_at_unix_ms = ts_val

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

    if win_thread_id and client_thread_id and win_thread_id != client_thread_id:
        identity_conflict = True
        conflict_notes.append(
            f"thread_id ({client_thread_id}) differs from window_id thread ({win_thread_id})"
        )

    workspace_candidates = extract_workspace_candidates(parsed_tm)
    workspace_id, workspace_id_source = resolve_workspace_id(workspace_candidates)

    return CodexIdentity(
        client_session_id=client_session_id,
        client_thread_id=client_thread_id,
        client_session_id_source=client_session_id_source,
        client_thread_id_source=client_thread_id_source,
        client_request_id=client_request_id,
        codex_window_id=codex_window_id,
        codex_window_thread_id=win_thread_id,
        codex_window_generation=win_gen,
        codex_window_parse_error=codex_window_parse_error,
        originator=originator,
        user_agent=user_agent,
        turn_metadata_raw=turn_metadata_raw,
        turn_metadata=turn_metadata,
        turn_id=turn_id,
        turn_started_at_unix_ms=turn_started_at_unix_ms,
        workspace_candidates=workspace_candidates if workspace_candidates else None,
        workspace_id=workspace_id,
        workspace_id_source=workspace_id_source,
        codex_turn_state_raw=codex_turn_state_raw,
        installation_id=installation_id,
        parent_thread_id=parent_thread_id,
        subagent=subagent,
        memgen_raw=memgen_raw,
        memgen_parse_error=memgen_parse_error,
        is_memgen=is_memgen,
        identity_conflict=identity_conflict,
        conflict_notes=conflict_notes if conflict_notes else None,
    )


def _extract_tool_name(tool: dict) -> str | None:
    if not isinstance(tool, dict):
        return None

    # OpenAI Responses/Codex tool declarations commonly use top-level name.
    name = tool.get("name")
    if isinstance(name, str):
        return name

    # Some function-call shapes nest the function name.
    function = tool.get("function")
    if isinstance(function, dict):
        nested_name = function.get("name")
        if isinstance(nested_name, str):
            return nested_name

    tool_type = tool.get("type")
    if isinstance(tool_type, str) and tool_type != "function":
        return tool_type

    return None


def extract_codex_body_metadata(body: dict) -> CodexRequestMetadata:
    if not isinstance(body, dict):
        return CodexRequestMetadata()

    prev_resp_id = body.get("previous_response_id")
    cache_key = body.get("prompt_cache_key")
    service_tier = body.get("service_tier")
    reasoning = body.get("reasoning")
    client_metadata = body.get("client_metadata")

    reasoning_effort = None
    reasoning_summary = None
    if isinstance(reasoning, dict):
        re_val = reasoning.get("effort")
        if isinstance(re_val, str):
            reasoning_effort = re_val
        rs_val = reasoning.get("summary")
        if isinstance(rs_val, str):
            reasoning_summary = rs_val

    client_inst_id = None
    if isinstance(client_metadata, dict):
        ci_val = client_metadata.get("x-codex-installation-id")
        if isinstance(ci_val, str):
            client_inst_id = ci_val

    tools = body.get("tools")
    tools_count = 0
    tool_names = None
    if isinstance(tools, list):
        tools_count = len(tools)
        tool_names = []
        for tool in tools:
            name = _extract_tool_name(tool)
            if name is not None:
                tool_names.append(name)

    has_output_schema = False
    text_verbosity = None
    text_field = body.get("text")
    if isinstance(text_field, dict):
        if "format" in text_field or "schema" in text_field:
            has_output_schema = True
        tv_val = text_field.get("verbosity")
        if isinstance(tv_val, str):
            text_verbosity = tv_val

    return CodexRequestMetadata(
        previous_response_id=prev_resp_id if isinstance(prev_resp_id, str) else None,
        prompt_cache_key=cache_key if isinstance(cache_key, str) else None,
        service_tier=service_tier if isinstance(service_tier, str) else None,
        reasoning_effort=reasoning_effort,
        reasoning_summary=reasoning_summary,
        text_verbosity=text_verbosity,
        parallel_tool_calls=body.get("parallel_tool_calls") if isinstance(body.get("parallel_tool_calls"), bool) else None,
        tool_choice=body.get("tool_choice") if isinstance(body.get("tool_choice"), (str, dict)) else None,
        store=body.get("store") if isinstance(body.get("store"), bool) else None,
        stream=body.get("stream") if isinstance(body.get("stream"), bool) else None,
        include=body.get("include") if isinstance(body.get("include"), list) else None,
        client_metadata=client_metadata if isinstance(client_metadata, dict) else None,
        client_installation_id_from_body=client_inst_id,
        has_output_schema=has_output_schema,
        tools_count=tools_count,
        tool_names=tool_names if tool_names else None,
    )


def resolve_memory_domain(identity: CodexIdentity, body_metadata: CodexRequestMetadata) -> str:
    """
    Resolves the memory_domain for the request.
    Current skeleton: default to "isolated".
    No inference from identity or body is allowed per the contract.
    """
    # Phase 1: Skeleton only. Default to isolated.
    # Future: check model/profile overrides for an explicit memory_domain.
    return "isolated"


def generate_qz_session_id(client_session_id: str | None) -> str:
    """
    Generates a QuantZhai internal session ID.
    If client_session_id is provided, use it as a base for stability.
    """
    if client_session_id and isinstance(client_session_id, str):
        # Use a prefix to distinguish from raw Codex session IDs
        return f"qz_sid_{client_session_id}"
    
    # Fallback to an anonymous session ID
    import uuid
    return f"qz_sid_anon_{uuid.uuid4().hex[:12]}"


def extract_codex_request_context(headers_raw: dict, body: dict) -> CodexRequestContext:
    identity = extract_codex_identity(headers_raw)
    body_metadata = extract_codex_body_metadata(body)

    # Detect cross-layer conflicts
    if identity.installation_id and body_metadata.client_installation_id_from_body:
        if identity.installation_id != body_metadata.client_installation_id_from_body:
            identity.identity_conflict = True
            note = (
                f"installation_id header ({identity.installation_id}) differs from "
                f"body client_metadata ({body_metadata.client_installation_id_from_body})"
            )
            if identity.conflict_notes is None:
                identity.conflict_notes = [note]
            else:
                identity.conflict_notes.append(note)

    memory_domain = resolve_memory_domain(identity, body_metadata)
    qz_session_id = generate_qz_session_id(identity.client_session_id)

    return CodexRequestContext(
        identity=identity,
        body_metadata=body_metadata,
        qz_session_id=qz_session_id,
        memory_domain=memory_domain,
    )
