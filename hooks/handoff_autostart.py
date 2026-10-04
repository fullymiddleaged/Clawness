#!/usr/bin/env python3
"""
Clawness — handoff auto-start (SessionStart, registered with asyncRewake).

When the handoff the previous session left is marked `**Autostart:** yes`, wake
Claude so this session begins on it without the user typing "carry on".
`asyncRewake` runs this in the background and, on exit code 2, wakes Claude with
stderr as a system reminder — the only way a hook can start work in an idle
interactive session. Every other path exits 0, which never wakes anything.

The guards (consent marker, freshness, once per handoff, startup/clear only) live
in clawness.handoff; see the auto-start block there. The short sleep lets the
session finish starting (and handoff_check render) so the wake lands on an idle
prompt rather than racing the startup. Opt out with CLAW_NO_HANDOFF_AUTOSTART=1 (or CLAW_NO_HANDOFF).
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hookutil import project_root, read_payload  # noqa: E402

WAKE = 2


def main() -> None:
    try:
        payload = read_payload()
        if payload is None or os.environ.get("CLAW_NO_HANDOFF"):
            sys.exit(0)
        root = project_root(payload.get("cwd") or os.getcwd())
        if root is None:
            sys.exit(0)

        from clawness.handoff import AUTOSTART_INSTRUCTION, autostart_due, claim_autostart

        path = autostart_due(root, payload.get("source") or "startup")
        if path is None:
            sys.exit(0)
        # Sleep BEFORE claiming: handoff_check runs concurrently and reads the
        # ledger to decide between the auto-start note and the ordinary one, so the
        # claim must land after it has rendered.
        try:
            delay = float(os.environ.get("CLAW_HANDOFF_AUTOSTART_DELAY", "2"))
        except ValueError:
            delay = 2.0
        time.sleep(max(0.0, min(delay, 20.0)))
        if not claim_autostart(root, path):
            sys.exit(0)
        print(AUTOSTART_INSTRUCTION, file=sys.stderr)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
    sys.exit(WAKE)


if __name__ == "__main__":
    main()
