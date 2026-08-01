#!/usr/bin/env python3
"""
Clawness — project memory bootstrap (SessionStart).

On the first session in a project, create `.clawness/memory.md` (the per-codebase
lessons-learned log that the UserPromptSubmit hook injects every turn) seeded with
a short how-to line, and inject a note so Claude tells the user once that the file
exists and how to use it ("remember this: ...").

Like git_check, this can't prompt the user directly — hooks only inject context,
and Claude relays it. It writes one small file the first time and is silent forever
after.

Gated to real projects: only inside a git work tree, never the home directory or
filesystem root, and never when CLAW_NO_MEMORY is set. If the file already exists
it stays silent. Fails open on any error — memory is a convenience, never a blocker.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Pins UTF-8 stdio at import and puts the repo on sys.path.
from _hookutil import git_root, read_payload, session_cwd  # noqa: E402

NOTE = (
    "[Clawness] Created .clawness/memory.md for this project — a lessons-learned log "
    "that Clawness injects into every prompt so hard-won fixes and recurring gotchas "
    "survive across sessions. Early in this session, briefly tell the user it now "
    "exists and that they can grow it by saying \"remember this: ...\" (you append a "
    "terse bullet to the file) or by editing it directly. Suggest committing it so "
    "the team shares the same knowledge. The user can silence this with CLAW_NO_MEMORY=1."
)


def main() -> None:
    payload = read_payload()
    if payload is None:
        sys.exit(0)

    if os.environ.get("CLAW_NO_MEMORY"):
        sys.exit(0)

    # Don't litter non-project locations (home directory or filesystem root).
    cwd_path = session_cwd(payload)
    if cwd_path is None:
        sys.exit(0)

    # Only auto-create inside a real project. Use the git work-tree root so the
    # file lands at the project root, not wherever the session happened to start.
    root = git_root(cwd_path)
    if root is None:
        sys.exit(0)

    try:
        memory_path = root / ".clawness" / "memory.md"
        if memory_path.exists():
            sys.exit(0)

        from clawness.core import MEMORY_TEMPLATE
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(MEMORY_TEMPLATE, encoding="utf-8")
    except Exception:
        # Couldn't write (permissions, read-only fs, deps not ready) — stay silent.
        sys.exit(0)

    print(NOTE)
    sys.exit(0)


if __name__ == "__main__":
    main()
