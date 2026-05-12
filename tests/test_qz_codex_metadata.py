import json
import unittest

from proxy.qz_codex_metadata import (
    TURN_METADATA_MAX_BYTES,
    WorkspaceCandidate,
    extract_codex_identity,
    extract_workspace_candidates,
    header_lookup,
    parse_codex_turn_metadata_header,
)


class HeaderLookupTests(unittest.TestCase):
    def test_header_lookup_case_insensitive(self):
        headers = {"Content-Type": "application/json", "X-Custom": "value"}
        self.assertEqual(header_lookup(headers, "content-type"), "application/json")
        self.assertEqual(header_lookup(headers, "CONTENT-TYPE"), "application/json")
        self.assertEqual(header_lookup(headers, "Content-Type"), "application/json")

    def test_header_lookup_exact_match_preferred(self):
        headers = {"Content-Type": "lower", "content-type": "also-lower"}
        self.assertEqual(header_lookup(headers, "content-type"), "also-lower")

    def test_header_lookup_returns_none_for_missing(self):
        self.assertIsNone(header_lookup({}, "missing"))
        self.assertIsNone(header_lookup({"a": "1"}, "b"))

    def test_header_lookup_handles_none_headers(self):
        self.assertIsNone(header_lookup(None, "x"))
        self.assertIsNone(header_lookup({}, None))


class ParseTurnMetadataTests(unittest.TestCase):
    def test_parse_turn_metadata_basic_session_thread_turn(self):
        raw = json.dumps({
            "session_id": "019e1ac2-b40c-7b03-ade6-4c7d8814af8c",
            "thread_id": "thread-abc-123",
            "turn_id": "turn-001",
        })
        parsed = parse_codex_turn_metadata_header(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["session_id"], "019e1ac2-b40c-7b03-ade6-4c7d8814af8c")
        self.assertEqual(parsed["thread_id"], "thread-abc-123")
        self.assertEqual(parsed["turn_id"], "turn-001")

    def test_parse_turn_metadata_turn_started_at_unix_ms(self):
        raw = json.dumps({
            "turn_started_at_unix_ms": 1747036800000,
        })
        parsed = parse_codex_turn_metadata_header(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["turn_started_at_unix_ms"], 1747036800000)

    def test_parse_turn_metadata_invalid_json_left_raw_only(self):
        result = parse_codex_turn_metadata_header("{invalid")
        self.assertIsNone(result)

    def test_parse_turn_metadata_non_object_json_left_raw_only(self):
        result = parse_codex_turn_metadata_header('["array", "not", "object"]')
        self.assertIsNone(result)
        result = parse_codex_turn_metadata_header('"string"')
        self.assertIsNone(result)
        result = parse_codex_turn_metadata_header('42')
        self.assertIsNone(result)

    def test_parse_turn_metadata_oversize_left_raw_only(self):
        # Build a payload slightly over the size limit
        key = '"k":'
        padding = TURN_METADATA_MAX_BYTES + 1
        raw = "{" + key + '"' + "x" * (padding - len(key) - 2) + '"}'
        self.assertGreater(len(raw.encode("utf-8")), TURN_METADATA_MAX_BYTES)
        result = parse_codex_turn_metadata_header(raw)
        self.assertIsNone(result)

    def test_parse_turn_metadata_returns_none_for_empty(self):
        self.assertIsNone(parse_codex_turn_metadata_header(""))
        self.assertIsNone(parse_codex_turn_metadata_header(None))


class WorkspaceCandidateTests(unittest.TestCase):
    def test_parse_turn_metadata_workspaces_remote_commit_dirty_state(self):
        raw = json.dumps({
            "workspaces": {
                "/home/user/project": {
                    "associated_remote_urls": {
                        "origin": "https://github.com/user/project.git",
                    },
                    "latest_git_commit_hash": "abc123def456",
                    "has_changes": True,
                }
            }
        })
        parsed = parse_codex_turn_metadata_header(raw)
        candidates = extract_workspace_candidates(parsed)
        self.assertEqual(len(candidates), 1)
        ws = candidates[0]
        self.assertEqual(ws.repo_root, "/home/user/project")
        self.assertEqual(ws.associated_remote_urls, {"origin": "https://github.com/user/project"})
        self.assertEqual(ws.latest_git_commit_hash, "abc123def456")
        self.assertEqual(ws.has_changes, True)

    def test_extract_workspace_candidates_handles_multiple_remotes(self):
        raw = json.dumps({
            "workspaces": {
                "/repo/a": {
                    "associated_remote_urls": {
                        "origin": "https://github.com/user/a.git",
                        "upstream": "https://github.com/org/a.git",
                    },
                    "latest_git_commit_hash": "aaa",
                },
                "/repo/b": {
                    "associated_remote_urls": {
                        "origin": "https://gitlab.com/user/b.git/",
                    },
                    "has_changes": False,
                },
            }
        })
        parsed = parse_codex_turn_metadata_header(raw)
        candidates = extract_workspace_candidates(parsed)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].repo_root, "/repo/a")
        self.assertEqual(
            candidates[0].associated_remote_urls,
            {"origin": "https://github.com/user/a", "upstream": "https://github.com/org/a"},
        )
        self.assertEqual(candidates[1].repo_root, "/repo/b")
        self.assertEqual(
            candidates[1].associated_remote_urls,
            {"origin": "https://gitlab.com/user/b"},
        )
        self.assertIsNone(candidates[1].latest_git_commit_hash)
        self.assertEqual(candidates[1].has_changes, False)

    def test_extract_workspace_candidates_handles_missing_git_fields(self):
        raw = json.dumps({
            "workspaces": {
                "/repo/only-path": {},
            }
        })
        parsed = parse_codex_turn_metadata_header(raw)
        candidates = extract_workspace_candidates(parsed)
        self.assertEqual(len(candidates), 1)
        ws = candidates[0]
        self.assertEqual(ws.repo_root, "/repo/only-path")
        self.assertIsNone(ws.associated_remote_urls)
        self.assertIsNone(ws.latest_git_commit_hash)
        self.assertIsNone(ws.has_changes)

    def test_extract_workspace_candidates_skips_non_string_remote_names(self):
        # JSON object keys are always strings after json.loads(), so exercise the
        # parser helper directly with an already-materialised dict.
        parsed = {
            "workspaces": {
                "/repo/a": {
                    "associated_remote_urls": {
                        "origin": "https://github.com/user/a.git",
                        123: "https://github.com/bad/key.git",
                    }
                }
            }
        }
        candidates = extract_workspace_candidates(parsed)
        self.assertEqual(candidates[0].associated_remote_urls, {"origin": "https://github.com/user/a"})

    def test_extract_workspace_candidates_empty_for_no_workspaces(self):
        self.assertEqual(extract_workspace_candidates(None), [])
        self.assertEqual(extract_workspace_candidates({}), [])
        self.assertEqual(extract_workspace_candidates({"workspaces": {}}), [])

    def test_workspace_candidates_are_marked_non_authoritative(self):
        # WorkspaceCandidate carries no authoritative flag by design.
        # Verify it's a plain dataclass without authority semantics.
        ws = WorkspaceCandidate(repo_root="/tmp/test")
        self.assertEqual(ws.repo_root, "/tmp/test")
        self.assertIsNone(ws.associated_remote_urls)
        self.assertIsNone(ws.latest_git_commit_hash)
        self.assertIsNone(ws.has_changes)
        self.assertFalse(hasattr(ws, "authoritative"))


class ExtractCodexIdentityTests(unittest.TestCase):
    def test_extract_codex_identity_reads_session_id_header(self):
        identity = extract_codex_identity({
            "session_id": "019e1ac2-b40c-7b03-ade6-4c7d8814af8c",
        })
        self.assertEqual(identity.client_session_id, "019e1ac2-b40c-7b03-ade6-4c7d8814af8c")
        self.assertEqual(identity.client_session_id_source, "session_id")

    def test_extract_codex_identity_reads_hyphenated_session_id_header(self):
        identity = extract_codex_identity({
            "session-id": "hyphenated-session-val",
        })
        self.assertEqual(identity.client_session_id, "hyphenated-session-val")
        self.assertEqual(identity.client_session_id_source, "session-id")

    def test_extract_codex_identity_detects_session_header_variant_conflict(self):
        identity = extract_codex_identity({
            "session_id": "underscore-session",
            "session-id": "hyphen-session",
        })
        self.assertEqual(identity.client_session_id, "underscore-session")
        self.assertTrue(identity.identity_conflict)
        self.assertTrue(any("session_id header variants disagree" in note for note in identity.conflict_notes))

    def test_extract_codex_identity_detects_thread_header_variant_conflict(self):
        identity = extract_codex_identity({
            "thread_id": "underscore-thread",
            "thread-id": "hyphen-thread",
        })
        self.assertEqual(identity.client_thread_id, "underscore-thread")
        self.assertTrue(identity.identity_conflict)
        self.assertTrue(any("thread_id header variants disagree" in note for note in identity.conflict_notes))

    def test_extract_codex_identity_reads_client_request_id(self):
        identity = extract_codex_identity({
            "x-client-request-id": "req-abc-123",
        })
        self.assertEqual(identity.client_request_id, "req-abc-123")

    def test_extract_codex_identity_reads_codex_window_id(self):
        identity = extract_codex_identity({
            "x-codex-window-id": "thread-abc:42",
        })
        self.assertEqual(identity.codex_window_id, "thread-abc:42")

    def test_extract_codex_identity_reads_originator_user_agent(self):
        identity = extract_codex_identity({
            "originator": "codex_exec",
            "user-agent": "codex_exec/0.125.0",
        })
        self.assertEqual(identity.originator, "codex_exec")
        self.assertEqual(identity.user_agent, "codex_exec/0.125.0")

    def test_turn_metadata_thread_id_used_when_header_absent(self):
        headers = {
            "x-codex-turn-metadata": json.dumps({
                "thread_id": "tm-thread-999",
            }),
        }
        identity = extract_codex_identity(headers)
        self.assertEqual(identity.client_thread_id, "tm-thread-999")
        self.assertEqual(identity.client_thread_id_source, "turn_metadata")

    def test_turn_metadata_session_id_conflict_detected(self):
        headers = {
            "session_id": "header-session-1",
            "x-codex-turn-metadata": json.dumps({
                "session_id": "metadata-session-2",
            }),
        }
        identity = extract_codex_identity(headers)
        self.assertTrue(identity.identity_conflict)
        self.assertIsNotNone(identity.conflict_notes)
        self.assertTrue(any("session_id" in note for note in identity.conflict_notes))

    def test_turn_metadata_thread_id_conflict_detected(self):
        headers = {
            "thread_id": "header-thread-1",
            "x-codex-turn-metadata": json.dumps({
                "thread_id": "metadata-thread-2",
            }),
        }
        identity = extract_codex_identity(headers)
        self.assertTrue(identity.identity_conflict)
        self.assertIsNotNone(identity.conflict_notes)
        self.assertTrue(any("thread_id" in note for note in identity.conflict_notes))

    def test_turn_started_at_ignores_float_and_bool(self):
        for value in (1234.5, True):
            with self.subTest(value=value):
                identity = extract_codex_identity({
                    "x-codex-turn-metadata": json.dumps({
                        "turn_started_at_unix_ms": value,
                    }),
                })
                self.assertIsNone(identity.turn_started_at_unix_ms)
                self.assertIsNone(identity.turn_metadata.turn_started_at_unix_ms)

    def test_turn_metadata_invalid_json_left_raw_only(self):
        headers = {
            "x-codex-turn-metadata": "{invalid",
        }
        identity = extract_codex_identity(headers)
        self.assertEqual(identity.turn_metadata_raw, "{invalid")
        self.assertIsNone(identity.turn_metadata)

    def test_turn_metadata_non_object_json_left_raw_only(self):
        headers = {
            "x-codex-turn-metadata": '["array", "value"]',
        }
        identity = extract_codex_identity(headers)
        self.assertEqual(identity.turn_metadata_raw, '["array", "value"]')
        self.assertIsNone(identity.turn_metadata)

    def test_turn_metadata_oversize_left_raw_only(self):
        key = '"k":'
        padding = TURN_METADATA_MAX_BYTES + 1
        oversize_val = "{" + key + '"' + "x" * (padding - len(key) - 2) + '"}'
        self.assertGreater(len(oversize_val.encode("utf-8")), TURN_METADATA_MAX_BYTES)
        headers = {
            "x-codex-turn-metadata": oversize_val,
        }
        identity = extract_codex_identity(headers)
        self.assertEqual(identity.turn_metadata_raw, oversize_val)
        self.assertIsNone(identity.turn_metadata)

    def test_case_insensitive_extraction(self):
        headers = {
            "SESSION_ID": "case-insensitive-session",
            "X-CLIENT-REQUEST-ID": "case-insensitive-req",
        }
        identity = extract_codex_identity(headers)
        self.assertEqual(identity.client_session_id, "case-insensitive-session")
        self.assertEqual(identity.client_request_id, "case-insensitive-req")

    def test_empty_headers_produces_minimal_identity(self):
        identity = extract_codex_identity({})
        self.assertIsNone(identity.client_session_id)
        self.assertIsNone(identity.client_thread_id)
        self.assertIsNone(identity.client_request_id)
        self.assertIsNone(identity.codex_window_id)
        self.assertIsNone(identity.originator)
        self.assertIsNone(identity.user_agent)
        self.assertIsNone(identity.turn_metadata_raw)
        self.assertFalse(identity.identity_conflict)
        self.assertIsNone(identity.conflict_notes)
        self.assertEqual(identity.workspace_id, "unknown")


class WorkspaceResolutionTests(unittest.TestCase):
    def test_resolve_workspace_id_from_remote_url(self):
        candidates = [
            WorkspaceCandidate(
                repo_root="/home/user/project",
                associated_remote_urls={"origin": "https://github.com/user/project"}
            )
        ]
        from proxy.qz_codex_metadata import resolve_workspace_id
        ws_id, source = resolve_workspace_id(candidates)
        self.assertEqual(ws_id, "remote:https://github.com/user/project")
        self.assertEqual(source, "codex_turn_metadata_remote")

    def test_resolve_workspace_id_from_repo_root_hash(self):
        candidates = [
            WorkspaceCandidate(repo_root="/home/user/project")
        ]
        from proxy.qz_codex_metadata import resolve_workspace_id
        ws_id, source = resolve_workspace_id(candidates)
        self.assertTrue(ws_id.startswith("path:"))
        self.assertEqual(source, "codex_turn_metadata_repo_root")
        # Verify stable hash
        import hashlib
        expected_hash = hashlib.sha256(b"/home/user/project").hexdigest()
        self.assertEqual(ws_id, f"path:{expected_hash}")

    def test_resolve_workspace_id_unknown_for_no_candidates(self):
        from proxy.qz_codex_metadata import resolve_workspace_id
        ws_id, source = resolve_workspace_id([])
        self.assertEqual(ws_id, "unknown")
        self.assertEqual(source, "unknown")


class MemoryDomainPolicyTests(unittest.TestCase):
    def test_memory_domain_defaults_to_isolated(self):
        from proxy.qz_codex_metadata import extract_codex_request_context
        ctx = extract_codex_request_context({}, {})
        self.assertEqual(ctx.memory_domain, "isolated")

    def test_no_memory_domain_inference(self):
        # Contract: Missing memory_domain must resolve to isolated.
        # It must NOT be inferred from client name, model name, etc.
        from proxy.qz_codex_metadata import extract_codex_request_context
        headers = {
            "originator": "codex_exec",
            "user-agent": "coding-assistant/1.0",
        }
        body = {
            "model": "qwen3.6turbo-coding",
            "tools": [{"type": "code_interpreter"}]
        }
        ctx = extract_codex_request_context(headers, body)
        self.assertEqual(ctx.memory_domain, "isolated")


class SessionIdTests(unittest.TestCase):
    def test_qz_session_id_maps_from_client_session_id(self):
        from proxy.qz_codex_metadata import extract_codex_request_context
        headers = {"session_id": "client-sid-123"}
        ctx = extract_codex_request_context(headers, {})
        self.assertEqual(ctx.qz_session_id, "qz_sid_client-sid-123")

    def test_missing_client_session_id_gets_anonymous_qz_session_id(self):
        from proxy.qz_codex_metadata import extract_codex_request_context
        ctx = extract_codex_request_context({}, {})
        self.assertTrue(ctx.qz_session_id.startswith("qz_sid_anon_"))


if __name__ == "__main__":
    unittest.main()
