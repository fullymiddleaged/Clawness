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

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Pins UTF-8 stdio at import (the handoff text is user prose and will contain
# non-ASCII sooner or later) and puts the repo on sys.path. The git work-tree root
# comes from here too, so the handoff is found from any subdirectory —
# tests/test_handoff.py imports `project_root` from this module.
from _hookutil import project_root, read_payload  # noqa: E402


def main() -> None:
    payload = read_payload()
    if payload is None:
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
