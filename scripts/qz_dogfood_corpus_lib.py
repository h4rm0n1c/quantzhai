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


if __name__ == "__main__":
    print("Shared helpers module - not a standalone script.")
