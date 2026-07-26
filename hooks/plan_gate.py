#!/usr/bin/env python3
"""
Clawness — plan gate hook.

Two responsibilities, by event:
  - PreToolUse on Write/Edit/MultiEdit/NotebookEdit: PROMPT (ask) for edits until
    the session has an approved plan (unless the gate is disabled). This is an
    approve dialog, never a hard block — the user can always click through.
  - PostToolUse on ExitPlanMode OR on a completed Write/Edit/…: the user just
    approved either a plan (native plan mode) or the first edit, so record
    approval for this session — the gate then clears itself and won't prompt
    again this session. The write-tool branch requires a ``tool_response`` as
    proof the call actually ran, so a declined ask never settles as approved.

Wire both in .claude-plugin/plugin.json (or settings.json):
  PreToolUse  matcher "Write|Edit|MultiEdit|NotebookEdit"
  PostToolUse matcher "ExitPlanMode|Write|Edit|MultiEdit|NotebookEdit"

Output: PreToolUse emits hookSpecificOutput.permissionDecision="ask" to prompt;
otherwise it exits 0 (defer to the normal permission flow). Fails open on any
error so a gate bug never breaks the session.
"""

import io
import json
import sys
from pathlib import Path

# The payload is raw UTF-8; without this a non-ASCII file path decodes as cp1252
# on Windows, so is_plan_file() compares a mangled path and mis-gates the write.
# isinstance narrows to the class that actually defines reconfigure() — sys.stdin
# is typed TextIO, which doesn't — and skips an already-replaced stream.
for _stream in (sys.stdin, sys.stdout):
    if isinstance(_stream, io.TextIOWrapper):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from clawness.plan import (
        find_project_root,
        gate_decision,
        record_session_approval,
        PLAN_APPROVAL_TOOL,
        WRITE_TOOLS,
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

        # PostToolUse: record approval and clear the gate for this session, on
        #   (a) native plan approval (ExitPlanMode), or
        #   (b) the first edit the user approved — a completed write tool. Require
        #       a tool_response there as proof the call actually ran, so a declined
        #       ask (which stops before PostToolUse, and would carry no response
        #       even if it ever fired) never settles as approved.
        if event == "PostToolUse":
            if tool_name == PLAN_APPROVAL_TOOL or (
                tool_name in WRITE_TOOLS and "tool_response" in payload
            ):
                try:
                    record_session_approval(root, session_id)
                except Exception:
                    pass
            sys.exit(0)

        # PreToolUse write gate. Pass the target path so writes to Claude Code's
        # plan file (which happen during plan mode, before approval) are never
        # gated.
        tool_input = payload.get("tool_input") or {}
        target_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        prompt, reason = gate_decision(root, tool_name, session_id, target_path)
        if not prompt:
            sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
