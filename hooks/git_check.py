#!/usr/bin/env python3
"""
Clawness — git presence check (SessionStart).

If the project isn't under version control, this injects a short note into
context so Claude will *ask* the user whether to initialize git. It never runs
`git init` itself — hooks can't interactively prompt, and initializing version
control is the user's call. The note tells Claude to ask and to act only on
explicit confirmation.

Silent when git is present, when git isn't installed, in non-project locations
(home dir / filesystem root), or when disabled via CLAW_NO_GIT_CHECK. Fails open.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The payload (incl. the project cwd) arrives as UTF-8 on stdin; on Windows stdin
# defaults to cp1252 and would mangle a non-ASCII project path. Pin UTF-8.
try:
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

# Directories never worth descending into when scanning for nested repos —
# heavy/vendored trees that won't be a project's own .git and would slow the scan.
_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__",
    "dist", "build", ".next", "out", "target", "vendor", ".cache",
    "site-packages", ".mypy_cache", ".pytest_cache", ".tox", ".gradle",
    "Pods", ".idea", ".vscode", "coverage", ".turbo",
}


def _git_in_tree(cwd_path: Path, max_depth: int = 4, max_dirs: int = 600) -> bool:
    """True if a git work tree governs cwd, an ancestor, OR a nearby descendant.

    `git rev-parse` only searches *upward*, so when Claude opens a workspace or
    monorepo parent whose actual repositories live in subfolders, the plain check
    sees no `.git` and wrongly reports "not under version control". We add a
    bounded *downward* scan (depth- and count-limited, skipping heavy dirs) so a
    tree that does use git isn't flagged. Fails closed (returns False) only after
    genuinely finding nothing within the bound.
    """
    # cwd or an ancestor — git walks upward on its own.
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd_path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip() == "true":
            return True
    except Exception:
        pass  # fall through to the descendant scan

    # Bounded breadth-first descendant scan for a nested `.git` (a directory in
    # normal clones, a file in submodules/worktrees — `.exists()` covers both).
    seen = 0
    frontier = [(cwd_path, 0)]
    while frontier:
        current, depth = frontier.pop()
        seen += 1
        if seen > max_dirs:
            break
        if (current / ".git").exists():
            return True
        if depth >= max_depth:
            continue
        try:
            children = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in children:
            try:
                if (entry.is_dir() and not entry.is_symlink()
                        and entry.name not in _SKIP_DIRS):
                    frontier.append((entry, depth + 1))
            except OSError:
                continue
    return False


NOTE = (
    "[Clawness] This project is not under version control (no git repository "
    "found). The plan gate blocks unplanned edits, but recovering from a bad "
    "edit relies on git. Early in this session, briefly tell the user and ask "
    "whether they'd like to initialize git for this project. Only run `git init` "
    "(and offer an initial commit — `git init` alone protects nothing until "
    "there's a commit) if the user agrees; never initialize version control "
    "without explicit confirmation. The user can silence this with "
    "CLAW_NO_GIT_CHECK=1."
)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if os.environ.get("CLAW_NO_GIT_CHECK"):
        sys.exit(0)

    cwd = payload.get("cwd") or os.getcwd()
    try:
        cwd_path = Path(cwd).resolve()
    except Exception:
        sys.exit(0)

    # Don't nag in non-project locations (home directory or filesystem root).
    try:
        if cwd_path == Path.home().resolve() or cwd_path.parent == cwd_path:
            sys.exit(0)
    except Exception:
        pass

    # Per-project opt-out marker.
    if (cwd_path / ".clawness" / "git-check-off").exists():
        sys.exit(0)

    # If git isn't installed, suggesting `git init` is pointless.
    if not shutil.which("git"):
        sys.exit(0)

    # Version control in use at cwd, an ancestor, or a project subfolder? Stay
    # silent. The descendant scan stops the false "no git" nag when Claude opens
    # a workspace/monorepo parent whose repos live in child folders.
    if _git_in_tree(cwd_path):
        sys.exit(0)

    # No git anywhere relevant → inject the note (SessionStart stdout becomes
    # Claude's context).
    print(NOTE)
    sys.exit(0)


if __name__ == "__main__":
    main()
