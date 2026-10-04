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
        from clawness.handoff import (
            AUTOSTART_USER_MESSAGE, autostart_claimed, autostart_due, find_handoff,
            render_handoff_note, suggest_session_name,
        )

        path = find_handoff(root)
        if not path:
            sys.exit(0)
        source = payload.get("source") or "startup"
        due = autostart_due(root, source)
        # Interactive auto-start is once per handoff (handoff_autostart claims it);
        # a second new session gets the ordinary conditional note.
        fresh = bool(due) and not autostart_claimed(root, due)
        note = render_handoff_note(path, autostart=fresh)
    except Exception:
        # Deps not ready, unreadable file, anything — a handoff is a convenience.
        sys.exit(0)

    if not note:
        sys.exit(0)
    if not due:
        print(note)
        sys.exit(0)

    # JSON form, to carry fields beyond context. initialUserMessage only applies
    # under `claude -p` (interactive ignores it), where it makes the pickup the
    # first turn with no prompt; deliberately not gated on the ledger, so a
    # scripted loop continues every run. sessionTitle replaces the "carry on"
    # title the session would otherwise get; Claude Code ignores it on `clear`.
    out = {"hookEventName": "SessionStart", "additionalContext": note,
           "initialUserMessage": AUTOSTART_USER_MESSAGE}
    try:
        title = suggest_session_name(path.read_text(encoding="utf-8")) if fresh else ""
    except Exception:
        title = ""
    if title:
        out["sessionTitle"] = title
    print(json.dumps({"hookSpecificOutput": out}))
    sys.exit(0)


if __name__ == "__main__":
    main()
