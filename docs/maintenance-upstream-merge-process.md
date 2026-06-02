# Upstream Merge Process

## Agent Autonomy Scope

The agent is authorised to perform the following without confirmation:

- `git fetch upstream master` and `git fetch origin` (read-only operations)
- Cherry-pick upstream commits into `main` and resolve conflicts per the rules below
- Merge TheTom's `origin/feature/turboquant-kv-cache` into `main` and resolve conflicts
- Push `main` to `fork/main` after any of the above
- Notify the user of what was done and any notable conflict resolutions

The agent must NOT do without explicit user confirmation:

- Open PRs or issues on any upstream repository (ggml-org/llama.cpp, TheTom/llama-cpp-turboquant, etc.)
- Create new branches on the fork
- Change the build target (`QZ_TQ_BRANCH` in `scripts/qz-env`)
- Add new model downloads or modify `var/models/`
- Any action that affects production uptime without prior notice

## Branch Architecture

```
upstream/master (ggml-org/llama.cpp)
  └─ origin/feature/turboquant-kv-cache (TheTom's fork)
      ├─ fork/main (our production branch)
      │   ├─ TheTom's base
      │   ├─ fix/srv-dining-philosophers-deadlock (bug chain)
      │   └─ feature/vram-http-metrics (VRAM/CUDA/load-timeout)
      │
      ├─ fork/fix/srv-dining-philosophers-deadlock
      │   Bug chain only — one squashed commit on TheTom base.
      │   Zombie slot, WIFSIGNALED, abort callback, stop_mutex,
      │   loading timeout, last_error/exit_signal. No auto-recover.
      │
      └─ fork/feature/vram-http-metrics
          VRAM patches only — when cleaned. Currently everything.
```

## When Upstream (ggml-org/llama.cpp) has a fix we need

ggml-org moves fast. TheTom cherry-picks selectively, often weeks behind.

```bash
# 1. Find the upstream commit we need
git fetch upstream master
git log upstream/master --oneline --no-merges -20 | grep "something we need"

# 2. Cherry-pick it directly onto our main
git checkout main
git cherry-pick <sha>

# 3. If it conflicts because TheTom doesn't have a prerequisite:
#    a) Find the prerequisite commits upstream
#    b) Cherry-pick them first (resolve conflicts once)
#    c) Then cherry-pick the target commit
git cherry-pick <prerequisite-sha>
# resolve conflicts, git add, git cherry-pick --continue
git cherry-pick <target-sha>
```

**Conflict rule:** Accept our version (main) for any file we patched (server-models.cpp, server-models.h, server.cpp, subprocess.h). Accept upstream's version for everything else. If the conflict is in our code, we need to understand why upstream changed it and decide whether to keep our change or adopt upstream's.

**Don't:**
- Create branches for upstream fixes (just cherry-pick directly onto main)
- Rebase our patch branches onto upstream (they're based on TheTom)
- Open PRs against upstream without explicit approval first

## When TheTom updates

TheTom merges upstream commits into his `feature/turboquant-kv-cache` branch. When that happens:

```bash
# 1. Fetch TheTom's latest
git fetch origin

# 2. Merge him into our main
git checkout main
git merge origin/feature/turboquant-kv-cache

# 3. Re-merge our two patch branches on top
git merge fix/srv-dining-philosophers-deadlock
git merge feature/vram-http-metrics
```

**If merge conflicts occur:**

For TheTom merge conflicts: accept his version unless it breaks our patches. Our patches touch specific files — if TheTom touched the same files, review both changes and merge manually.

For our patch branch merge conflicts: accept our version (`--ours`). Our patches are small and targeted. If TheTom's changes conflict with ours, we need to understand why and decide which approach is correct.

**If TheTom's merge is large (e.g., he syncs 1000+ upstream commits):**

First strategy: use `git merge --no-commit origin/feature/turboquant-kv-cache`, resolve all conflicts, commit. Then merge our patch branches — they should apply cleanly because the conflict resolution brought in TheTom's changes.

Second strategy (if the patch branches also conflict): re-create the patch commits from the bug chain or VRAM commits. This is rare — our patches touch fundamentally different code than TheTom/upstream.

## Never

- Open a PR against upstream without explicit approval
- Submit work-in-progress branches
- Add workaround code when the root cause fix is simpler
- Rebase production branches (force push only in emergency)
- Detach threads with dangling references
- Guess at root causes — trace the actual execution path
