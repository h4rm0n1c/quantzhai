# Shared helpers for qz-dogfood-corpus-* scripts.
# Python standard library only. No dependencies.

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def default_corpus_root() -> str:
    return os.path.expanduser("~/turboquant/qz-dogfood-corpus")


def corpus_root() -> str:
    return os.environ.get("QZ_DOGFOOD_CORPUS_ROOT") or default_corpus_root()


def default_work_root() -> str:
    return "/tmp/qz-dogfood-work"


def work_root() -> str:
    return os.environ.get("QZ_DOGFOOD_WORK_ROOT") or default_work_root()


def run_id() -> str:
    env = os.environ.get("QZ_DOGFOOD_RUN_ID")
    if env:
        return env
    return time.strftime("run-%Y%m%d-%H%M%S")


def keep_scratch() -> bool:
    return os.environ.get("QZ_DOGFOOD_KEEP_SCRATCH", "0") == "1"


def selected_repo_ids() -> Optional[List[str]]:
    raw = os.environ.get("QZ_DOGFOOD_REPOS")
    if raw:
        return [r.strip() for r in raw.split(",") if r.strip()]
    return None


def load_repos_config(path: Optional[str] = None) -> List[Dict[str, Any]]:
    if path is None:
        root = os.environ.get("QZ_ROOT") or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        path = os.path.join(root, "config", "dogfood", "repos.json")
    with open(path, "r") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "repos" not in data:
        raise ValueError("repos.json must contain a 'repos' list")
    repos = data["repos"]
    if not isinstance(repos, list) or len(repos) == 0:
        raise ValueError("repos.json 'repos' must be a non-empty list")
    required = {"id", "url", "cache_name", "scratch_name"}
    for r in repos:
        missing = required - set(r.keys())
        if missing:
            raise ValueError(
                f"Repo entry missing keys: {missing} (entry: {r.get('id', 'unknown')})"
            )
    return repos


def selected_repos(repos: List[Dict[str, Any]], ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if ids is None:
        return list(repos)
    by_id = {r["id"]: r for r in repos}
    result = []
    for rid in ids:
        if rid not in by_id:
            print(f"Warning: unknown repo id '{rid}', skipping", file=sys.stderr)
        else:
            result.append(by_id[rid])
    return result


def run_git(args: List[str], cwd: Optional[str] = None, timeout: int = 120) -> subprocess.CompletedProcess:
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def mirror_path(repo: Dict[str, Any]) -> str:
    return os.path.join(corpus_root(), "cache", repo["cache_name"])


def scratch_path(repo: Dict[str, Any], rid: str) -> str:
    return os.path.join(work_root(), rid, repo["scratch_name"])


def runs_dir() -> str:
    return os.path.join(corpus_root(), "runs")


def run_dir(rid: str) -> str:
    return os.path.join(runs_dir(), rid)


def ensure_mirror(repo: Dict[str, Any]) -> Optional[str]:
    mp = mirror_path(repo)
    if os.path.isdir(mp):
        return mp
    url = repo["url"]
    print(f"Cloning mirror: {url} -> {mp}")
    result = run_git(["clone", "--mirror", url, mp])
    if result.returncode != 0:
        print(f"Failed to clone mirror for {repo['id']}: {result.stderr.strip()}", file=sys.stderr)
        return None
    return mp


def update_mirror(repo: Dict[str, Any]) -> bool:
    mp = mirror_path(repo)
    if not os.path.isdir(mp):
        mp = ensure_mirror(repo)
        if mp is None:
            return False
        return True
    print(f"Updating mirror: {repo['id']} ({mp})")
    result = run_git(["remote", "update", "--prune"], cwd=mp)
    if result.returncode != 0:
        print(f"Failed to update mirror for {repo['id']}: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def resolve_ref(repo: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    mp = mirror_path(repo)
    if not os.path.isdir(mp):
        return None, None
    ref = repo.get("ref", "HEAD")
    # Mirror clones store refs under refs/heads/, not refs/remotes/origin/
    result = run_git(["rev-parse", f"refs/heads/{ref}"], cwd=mp)
    if result.returncode == 0:
        sha = result.stdout.strip()
        ref_used = f"heads/{ref}"
        return ref_used, sha
    # Fallback: try origin/ref for non-mirror clones
    result = run_git(["rev-parse", f"refs/remotes/origin/{ref}"], cwd=mp)
    if result.returncode == 0:
        sha = result.stdout.strip()
        ref_used = f"origin/{ref}"
        return ref_used, sha
    # Fallback to remote HEAD
    result = run_git(["rev-parse", "refs/remotes/origin/HEAD"], cwd=mp)
    if result.returncode == 0:
        sha = result.stdout.strip()
        ref_used = "origin/HEAD"
        return ref_used, sha
    # Last resort: try HEAD directly
    result = run_git(["rev-parse", "HEAD"], cwd=mp)
    if result.returncode == 0:
        sha = result.stdout.strip()
        ref_used = "HEAD"
        return ref_used, sha
    return None, None


def stage_repo(repo: Dict[str, Any], rid: str, force: bool = False) -> Optional[str]:
    mp = mirror_path(repo)
    if not os.path.isdir(mp):
        print(f"Cache missing for {repo['id']}, cannot stage", file=sys.stderr)
        return None
    sp = scratch_path(repo, rid)
    if os.path.isdir(sp):
        if not force:
            print(f"Scratch dir exists for {repo['id']}: {sp} (use --force or clean first)", file=sys.stderr)
            return sp
        shutil.rmtree(sp)
    ref_used, sha = resolve_ref(repo)
    if sha is None:
        print(f"Cannot resolve ref for {repo['id']}", file=sys.stderr)
        return None
    print(f"Cloning scratch: {repo['id']} @ {sha[:12]}")
    url = mp
    result = run_git(["clone", url, sp])
    if result.returncode != 0:
        print(f"Failed to clone scratch for {repo['id']}: {result.stderr.strip()}", file=sys.stderr)
        return None
    result = run_git(["checkout", sha], cwd=sp)
    if result.returncode != 0:
        print(f"Failed to checkout {sha} in scratch for {repo['id']}: {result.stderr.strip()}", file=sys.stderr)
        return None
    return sp


def repo_scratch_status(path: str) -> Dict[str, Any]:
    result = run_git(["rev-parse", "HEAD"], cwd=path)
    head = result.stdout.strip() if result.returncode == 0 else "unknown"
    result = run_git(["status", "--short"], cwd=path)
    status_lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    dirty = len(status_lines) > 0
    return {
        "head": head,
        "dirty": dirty,
        "status_lines": status_lines,
    }


def write_json(path: str, data: Any) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_md(path: str, title: str, lines: List[str]) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w") as f:
        f.write(f"# {title}\n\n")
        for line in lines:
            f.write(line + "\n")


def safe_remove(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        shutil.rmtree(path)
        print(f"Removed: {path}")
    except OSError as e:
        print(f"Failed to remove {path}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Stage 6.8: targeted file selection helpers
# Standard library only. No proxy imports.
# ---------------------------------------------------------------------------

# Extensions that indicate binary content — skip these entirely.
BINARY_EXTENSIONS: frozenset = frozenset([
    ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".svg", ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".ogg", ".wav", ".avi", ".mov",
    ".bin", ".so", ".a", ".o", ".class", ".jar",
    ".zip", ".tar", ".gz", ".xz", ".bz2", ".7z", ".rar",
    ".pdf", ".exe", ".dll", ".dylib",
    ".db", ".sqlite", ".sqlite3",
])

# Max bytes read from any single file in targeted selection.
FILE_READ_CAP_BYTES: int = 8000

# Max files expanded from one directory target.
DIR_EXPAND_LIMIT: int = 4

# Total max files selected (targeted + fallback combined).
TARGETED_FILE_CAP: int = 15

# Per-repo targeted paths.
# Each entry: (path_spec, category_label)
# path_spec rules:
#   ends with "/"   → directory; expand recursively up to DIR_EXPAND_LIMIT files
#   contains "*"    → glob pattern applied to repo root only
#   otherwise       → exact relative path from repo root
REPO_TARGET_PATHS: Dict[str, List[Tuple[str, str]]] = {
    "linuxstreamtools": [
        ("README.md",   "docs"),
        ("docs/",       "docs"),
        ("scripts/",    "scripts"),
        ("*.sh",        "scripts"),
    ],
    "quantzhai": [
        ("proxy/qz_responses.py",           "source"),
        ("proxy/qz_survival_weight.py",     "source"),
        ("tests/test_qz_survival_weight.py","tests"),
        ("tests/test_qz_llm_compaction.py", "tests"),
        ("config/default/compaction.json",  "config"),
        ("config/dogfood/repos.json",       "config"),
        ("docs/compaction-live-dogfood.md", "docs"),
    ],
    "click": [
        ("pyproject.toml",  "build"),
        ("src/click/",      "source"),
        ("tests/",          "tests"),
        ("docs/",           "docs"),
        ("README.rst",      "docs"),
        ("README.md",       "docs"),
        ("CHANGES.rst",     "changelog"),
    ],
    "p-limit": [
        ("package.json",    "build"),
        ("index.js",        "source"),
        ("index.d.ts",      "source"),
        ("test.js",         "tests"),
        ("readme.md",       "docs"),
        ("README.md",       "docs"),
    ],
    "bubbletea": [
        ("go.mod",          "build"),
        ("go.sum",          "build"),
        ("tea.go",          "source"),
        ("examples/",       "examples"),
        ("README.md",       "docs"),
        ("*.go",            "source"),
    ],
    "fd": [
        ("Cargo.toml",      "build"),
        ("Cargo.lock",      "build"),
        ("src/",            "source"),
        ("tests/",          "tests"),
        ("completions/",    "completions"),
        ("README.md",       "docs"),
    ],
    "fmt": [
        ("CMakeLists.txt",  "build"),
        ("include/fmt/",    "source"),
        ("src/",            "source"),
        ("test/",           "tests"),
        ("doc/",            "docs"),
        ("README.rst",      "docs"),
        ("README.md",       "docs"),
    ],
    "stb": [
        ("README.md",           "docs"),
        ("stb_image.h",         "source"),
        ("stb_truetype.h",      "source"),
        ("stb_sprintf.h",       "source"),
        ("docs/",               "docs"),
    ],
}


def is_binary_path(path: "Path") -> bool:
    """Return True if the file extension indicates binary content."""
    return Path(path).suffix.lower() in BINARY_EXTENSIONS


def is_readable_text_file(path: "Path") -> bool:
    """Return True if the path looks like a readable text file.

    Checks extension and ignores .git internals.
    Does NOT read the file; use _has_binary_bytes for content-based detection.
    """
    p = Path(path)
    if ".git" in p.parts:
        return False
    if is_binary_path(p):
        return False
    return True


def _has_binary_bytes(raw: bytes, check_bytes: int = 512) -> bool:
    """Return True if raw bytes contain a NUL, suggesting binary content."""
    return b"\x00" in raw[:check_bytes]


def select_targeted_files(
    repo_id: str,
    scratch: str,
    max_files: int = TARGETED_FILE_CAP,
    max_bytes_per_file: int = FILE_READ_CAP_BYTES,
    dir_expand_limit: int = DIR_EXPAND_LIMIT,
) -> List[Dict[str, Any]]:
    """Select files for a corpus scenario using per-repo targeted paths.

    Returns a list of dicts:
        path         – absolute path string
        rel_path     – relative to scratch root
        reason       – "exact:<category>", "dir:<category>",
                       "glob:<category>", or "fallback:sorted"
        bytes_read   – characters read (may be capped)
        capped       – True if file was truncated to max_bytes_per_file
        text         – file text (possibly truncated)

    File selection strategy:
    1. Try configured REPO_TARGET_PATHS for the repo.
    2. For each target spec, expand files in deterministic sorted order.
    3. Skip .git paths, binary-extension files, and NUL-byte content.
    4. Deduplicate: each file path appears at most once.
    5. Stop when max_files reached.
    6. Fallback: if nothing was selected (no targets configured or all
       missing), use generic sorted rglob traversal.

    Standard library only. No proxy imports.
    """
    import fnmatch

    src = Path(scratch)
    if not src.is_dir():
        return []

    targets = REPO_TARGET_PATHS.get(repo_id, [])
    selected: List[Tuple["Path", str]] = []  # (abs_path, reason)
    seen: set = set()

    def _add(f: "Path", reason: str) -> bool:
        """Attempt to add file; return True if added."""
        key = str(f.resolve())
        if key in seen:
            return False
        if not is_readable_text_file(f):
            return False
        seen.add(key)
        selected.append((f, reason))
        return True

    if targets:
        for spec, category in targets:
            if len(selected) >= max_files:
                break
            if spec.endswith("/"):
                # Directory: sorted recursive expansion up to dir_expand_limit
                d = src / spec.rstrip("/")
                if d.is_dir():
                    count = 0
                    for f in sorted(d.rglob("*")):
                        if len(selected) >= max_files:
                            break
                        if not f.is_file():
                            continue
                        if _add(f, f"dir:{category}"):
                            count += 1
                            if count >= dir_expand_limit:
                                break
            elif "*" in spec:
                # Glob: match files in repo root only (deterministic)
                for f in sorted(src.iterdir()):
                    if len(selected) >= max_files:
                        break
                    if f.is_file() and fnmatch.fnmatch(f.name, spec):
                        _add(f, f"glob:{category}")
            else:
                # Exact relative path
                f = src / spec
                if f.is_file():
                    _add(f, f"exact:{category}")

    # Fallback: generic sorted traversal if nothing was selected
    if not selected:
        for f in sorted(src.rglob("*")):
            if len(selected) >= max_files:
                break
            if not f.is_file():
                continue
            if _add(f, "fallback:sorted"):
                pass  # continue until cap

    # Read file contents
    result: List[Dict[str, Any]] = []
    for f, reason in selected:
        try:
            raw = f.read_bytes()
        except Exception:
            continue
        if _has_binary_bytes(raw):
            continue
        text = raw.decode("utf-8", errors="replace")
        capped = False
        if len(text) > max_bytes_per_file:
            text = text[:max_bytes_per_file]
            capped = True
        result.append({
            "path": str(f),
            "rel_path": str(f.relative_to(src)),
            "reason": reason,
            "bytes_read": len(text),
            "capped": capped,
            "text": text,
        })

    return result


# ---------------------------------------------------------------------------
# Stage 6.8: artifact subdirectory helpers
# ---------------------------------------------------------------------------

def summaries_dir(rdir: str) -> str:
    """Return path to summaries/ subdir under run_dir."""
    return os.path.join(rdir, "summaries")


def hints_dir(rdir: str) -> str:
    """Return path to survival-hints/ subdir under run_dir."""
    return os.path.join(rdir, "survival-hints")


def selected_files_dir(rdir: str) -> str:
    """Return path to selected-files/ subdir under run_dir."""
    return os.path.join(rdir, "selected-files")


def scenario_artifact_name(repo_id: str, scenario_id: str, suffix: str) -> str:
    """Return artifact filename for a repo/scenario pair, e.g. click-deep-coverage.summary.md"""
    return f"{repo_id}-{scenario_id}{suffix}"


def write_text_artifact(path: str, content: str) -> None:
    """Write a text artifact file, creating parent directories as needed."""
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    print("Shared helpers module - not a standalone script.")
