#!/usr/bin/env python3
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SearchPolicySelection:
    policy: dict
    policy_path: str
    default_profile: str
    source: str
    override_policy_file: str = ""
    error: str = ""

    def metadata(self) -> dict:
        out = {
            "source": self.source,
            "policy_path": self.policy_path,
            "default_profile": self.default_profile,
        }
        if self.override_policy_file:
            out["override_policy_file"] = self.override_policy_file
        if self.error:
            out["error"] = self.error
        return out


def _search_override_config(selected_model: dict | None) -> dict:
    overrides = {}
    if isinstance(selected_model, dict):
        overrides = selected_model.get("overrides") or {}
    if not isinstance(overrides, dict):
        return {}

    search_cfg = overrides.get("search") or {}
    if not isinstance(search_cfg, dict):
        search_cfg = {}

    policy_file = (
        search_cfg.get("policy_file")
        or search_cfg.get("search_policy_file")
        or overrides.get("search_policy_file")
    )
    default_profile = (
        search_cfg.get("default_profile")
        or search_cfg.get("profile")
        or overrides.get("search_policy_profile")
        or overrides.get("web_search_profile")
    )

    out = {}
    if isinstance(policy_file, str) and policy_file.strip():
        out["policy_file"] = policy_file.strip()
    if isinstance(default_profile, str) and default_profile.strip():
        out["default_profile"] = default_profile.strip()
    return out


def _resolve_policy_path(policy_file: str, root: Path) -> Path:
    raw = Path(policy_file).expanduser()
    if raw.is_absolute():
        return raw

    candidates = [
        root / "config" / "user" / raw,
        root / raw,
        root / "config" / "default" / raw,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _read_policy_file(path: Path) -> tuple[dict, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except Exception as exc:
        return {}, str(exc)


def resolve_search_policy_selection(
    *,
    base_policy: dict | None,
    base_policy_path: str = "",
    selected_model: dict | None = None,
    root: Path | str | None = None,
) -> SearchPolicySelection:
    base = base_policy if isinstance(base_policy, dict) else {}
    cfg = _search_override_config(selected_model)
    default_profile = cfg.get("default_profile", "")

    if cfg.get("policy_file"):
        repo_root = Path(root or ".").expanduser().resolve()
        policy_path = _resolve_policy_path(cfg["policy_file"], repo_root)
        policy, error = _read_policy_file(policy_path)
        if isinstance(policy, dict) and policy:
            return SearchPolicySelection(
                policy=policy,
                policy_path=str(policy_path),
                default_profile=default_profile,
                source="model_override",
                override_policy_file=cfg["policy_file"],
            )
        return SearchPolicySelection(
            policy=base,
            policy_path=str(base_policy_path or ""),
            default_profile=default_profile,
            source="base_fallback",
            override_policy_file=cfg["policy_file"],
            error=f"failed to load search policy override {policy_path}: {error or 'empty policy'}",
        )

    return SearchPolicySelection(
        policy=base,
        policy_path=str(base_policy_path or ""),
        default_profile=default_profile,
        source="base",
    )
