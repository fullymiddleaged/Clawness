#!/usr/bin/env python3
"""
Clawness — access guard hook (PreToolUse + PostToolUse).

Forces a human decision on tool calls that look like exfiltration, destruction,
or a scope-escape, even when the user has broadly allow-listed the tool — the
in-session companion to the plan gate. Decision logic lives in
``clawness/guard.py``; this script is just the stdin/stdout wrapper.

Two responsibilities, by event (mirrors plan_gate.py's dual-event dispatch):
  - PreToolUse: classify the call. ALLOW is silent. DENY blocks (never
    suppressed by the ledger). ASK records the target as "pending" and
    surfaces the prompt — but does NOT yet mark it as settled for the session,
    since we don't know if the user will approve.
  - PostToolUse (same matcher): the tool call actually ran, meaning any ASK
    prompt was approved (a decline stops the call before PostToolUse fires) —
    promote that target's ledger entry to "confirmed" so a repeat doesn't
    re-ask. Re-classifies to confirm this call was actually the kind that
    would have asked, rather than confirming every tool call unconditionally.
    Defense-in-depth: we ALSO require the payload to carry a ``tool_response``
    (present only on a real completion). So even if a future Claude Code build
    ever fired PostToolUse for a declined/aborted call, the absence of a
    tool_response keeps the entry "pending" and the guard correctly re-asks —
    the confirm no longer rests solely on the "decline stops the call" premise.

Wire in .claude-plugin/plugin.json:
  PreToolUse  matcher "Bash|Write|Edit|MultiEdit|NotebookEdit|Read"
  PostToolUse matcher "Bash|Write|Edit|MultiEdit|NotebookEdit|Read"

Output: PreToolUse emits hookSpecificOutput.permissionDecision = "deny" | "ask"
to block or prompt; otherwise exits 0 (defer to the normal permission flow).
Coexists with plan_gate (separate PreToolUse entry); Claude Code resolves
multiple hooks as deny > ask > allow. Fails OPEN on any error so a guard bug
never breaks a session. Opt out with CLAW_NO_ACCESS_GUARD=1.
"""

import io
import json
import os
import sys
from pathlib import Path

# stdin/stdout arrive as UTF-8; on Windows they default to cp1252 and would
# mangle non-ASCII paths in the payload or reason. Pin UTF-8.
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
    from clawness.guard import (
        ALLOW,
        ASK,
        already_asked,
        classify_tool_call,
        confirm_ask,
        dedup_key,
        record_ask,
    )
    from clawness.plan import find_project_root
except Exception:
    sys.exit(0)


def main() -> None:
    if os.environ.get("CLAW_NO_ACCESS_GUARD"):
        sys.exit(0)

    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        event = payload.get("hook_event_name", "") or "PreToolUse"
        tool_name = payload.get("tool_name", "") or ""
        tool_input = payload.get("tool_input") or {}
        session_id = payload.get("session_id", "") or ""
        cwd = payload.get("cwd") or None
        root = find_project_root(Path(cwd) if cwd else None)

        decision, reason = classify_tool_call(tool_name, tool_input, root)

        if event == "PostToolUse":
            # The call completed, so any ASK prompt for it was approved — settle
            # the ledger. Only for calls that would actually have asked; nothing
            # to do for ALLOW/DENY (DENY never reaches PostToolUse — it's blocked).
            # Require a tool_response as proof the call actually ran, so a declined
            # call (which shouldn't fire PostToolUse at all, and if it ever did,
            # would carry no response) never settles as confirmed.
            if decision == ASK and "tool_response" in payload:
                confirm_ask(root, session_id, dedup_key(tool_name, tool_input))
            sys.exit(0)

        # --- PreToolUse ---
        if decision == ALLOW or not reason:
            sys.exit(0)

        # Ask at most once per target per session, so a confirmed-OK out-of-project
        # write or known-host upload doesn't re-prompt on every repeat. Denies are
        # never suppressed. A PENDING (not-yet-confirmed) prior ask does NOT count
        # as already-asked — see confirm_ask above.
        if decision == ASK:
            key = dedup_key(tool_name, tool_input)
            if already_asked(root, session_id, key):
                sys.exit(0)
            record_ask(root, session_id, key)
    except Exception:
        sys.exit(0)  # fail open

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
