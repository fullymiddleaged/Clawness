#!/usr/bin/env python3
"""
Clawness — session handoff pickup (SessionStart).

If the previous session left a handoff at `<project>/.clawness/handoff.md`, inject
it so this session opens by telling the user where things left off. The user should
not have to remember the file exists, know its path, or ask for it — that's the
whole point of writing one.

Mirrors memory_init: gated to git work trees, never the home directory or filesystem
root, silent when there's nothing to say, and fails open on every error. Opt out with
CLAW_NO_HANDOFF=1.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The payload (incl. the project cwd) arrives as UTF-8 on stdin; on Windows stdin
# defaults to cp1252 and would mangle a non-ASCII project path. Pin UTF-8 both ways —
# the handoff text itself is user prose and will contain non-ASCII sooner or later.
try:
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def project_root(cwd: str) -> Path | None:
    """The git work-tree root, so the handoff is found from any subdirectory."""
    try:
        cwd_path = Path(cwd).resolve()
    except Exception:
        return None
    try:
        if cwd_path == Path.home().resolve() or cwd_path.parent == cwd_path:
            return None
    except Exception:
        pass
    if not shutil.which("git"):
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return Path(r.stdout.strip()).resolve()
    except Exception:
        return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if os.environ.get("CLAW_NO_HANDOFF"):
        sys.exit(0)

    root = project_root(payload.get("cwd") or os.getcwd())
    if root is None:
        sys.exit(0)

    try:
        from clawness.handoff import find_handoff, render_handoff_note

        path = find_handoff(root)
        if not path:
            sys.exit(0)
        note = render_handoff_note(path)
    except Exception:
        # Deps not ready, unreadable file, anything — a handoff is a convenience.
        sys.exit(0)

    if note:
        print(note)
    sys.exit(0)


if __name__ == "__main__":
    main()
