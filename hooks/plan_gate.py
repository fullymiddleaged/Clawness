#!/usr/bin/env python3
"""
Clawness — plan gate hook.

Two responsibilities, by event:
  - PreToolUse on Write/Edit/MultiEdit/NotebookEdit: deny edits until the
    session has an approved plan (unless the gate is disabled).
  - PostToolUse on ExitPlanMode: the user just approved a plan in native plan
    mode, so record approval for this session — the gate then clears itself.

Wire both in .claude-plugin/plugin.json (or settings.json):
  PreToolUse  matcher "Write|Edit|MultiEdit|NotebookEdit"
  PostToolUse matcher "ExitPlanMode"

Output: PreToolUse emits hookSpecificOutput.permissionDecision="deny" to block;
otherwise it exits 0 (defer to the normal permission flow). Fails open on any
error so a gate bug never breaks the session.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from clawness.plan import (
        find_project_root,
        gate_decision,
        record_session_approval,
        PLAN_APPROVAL_TOOL,
    )
except Exception:
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # Everything up to the decision is wrapped so a malformed payload (non-dict
    # JSON, unexpected shapes) or a root-detection error exits 0 cleanly — the
    # gate must fail OPEN, never crash the tool call with a traceback.
    try:
        event = payload.get("hook_event_name", "")
        tool_name = payload.get("tool_name", "")
        session_id = payload.get("session_id", "") or ""
        cwd = payload.get("cwd") or None
        root = find_project_root(Path(cwd) if cwd else None)

        # Native plan approval: record it and clear the gate for this session.
        if tool_name == PLAN_APPROVAL_TOOL and event == "PostToolUse":
            try:
                record_session_approval(root, session_id)
            except Exception:
                pass
            sys.exit(0)

        # Write gate. Pass the target path so writes to Claude Code's plan file
        # (which happen during plan mode, before approval) are never blocked.
        tool_input = payload.get("tool_input") or {}
        target_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        block, reason = gate_decision(root, tool_name, session_id, target_path)
        if not block:
            sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
