#!/usr/bin/env python3
"""
Merge the Clawness hook into Claude Code's settings.json.

Called by install.ps1 and install.sh — not meant to be run directly,
but safe to do so:

    python setup_settings.py <hook_script_path> [--settings <path>] [--dry-run]

Handles every state:
  - settings.json doesn't exist         → creates it
  - exists but no "hooks" key           → adds it
  - has "hooks" but no UserPromptSubmit  → adds the event
  - already has UserPromptSubmit         → appends if not duplicate
  - already has this exact hook          → skips (idempotent)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def claude_config_dir() -> Path:
    """Base Claude Code config dir. Honors CLAUDE_CONFIG_DIR (which relocates
    ~/.claude), falling back to ~/.claude. Tolerates a comma-separated value by
    using the first entry."""
    v = os.environ.get("CLAUDE_CONFIG_DIR")
    if v:
        first = v.split(",")[0].strip()
        if first:
            return Path(first).expanduser()
    return Path.home() / ".claude"


def default_settings_path() -> Path:
    """settings.json inside the Claude config dir (honors CLAUDE_CONFIG_DIR)."""
    return claude_config_dir() / "settings.json"


def build_hook_entry(hook_script: Path, timeout: int = 30) -> dict:
    """Build the hook JSON object pointing at our script.

    Uses a portable interpreter picker (python3 -> python -> py) rather than a
    single hardcoded command, so the hook runs whatever Python actually exists
    — matching the plugin's hooks and avoiding a mismatch with whatever the
    installer happened to detect (e.g. the Windows `py` launcher). Claude Code
    runs hook commands via a POSIX shell (sh / Git Bash on Windows), so the
    loop is portable across all platforms."""
    # Forward slashes even on Windows — Claude Code / Git Bash handle them fine.
    script_path = str(hook_script.resolve()).replace("\\", "/")
    command = (
        'for p in python3 python py; do '
        f'command -v "$p" >/dev/null 2>&1 && exec "$p" "{script_path}"; done'
    )
    return {
        "type": "command",
        "command": command,
        "timeout": timeout,
    }


def hook_already_present(events: list, hook_script: Path) -> bool:
    """Check if our hook is already registered (by script path substring)."""
    needle = hook_script.resolve().name  # "claude_hook.py"
    for event_group in events:
        for h in event_group.get("hooks", []):
            if needle in h.get("command", ""):
                return True
    return False


# Script names that identify a Clawness hook in settings.json.
CLAW_HOOK_SCRIPTS = (
    "claude_hook.py",
    "compress_output.py",
    "plan_gate.py",
    "access_guard.py",
    "trust_ledger.py",
    "git_check.py",
    "ensure_deps.py",
    "memory_init.py",
    "handoff_check.py",
    "stack_detect.py",
)


def _is_clawness_hook(h: dict) -> bool:
    cmd = h.get("command", "") if isinstance(h, dict) else ""
    return any(name in cmd for name in CLAW_HOOK_SCRIPTS)


def unmerge(settings_path: Path, dry_run: bool = False) -> str:
    """Remove only Clawness hooks from settings.json, leaving everything else
    untouched. Drops hook groups that become empty, and event keys that become
    empty. Safe to run when nothing is installed."""
    if not settings_path.exists():
        return f"OK: nothing to remove ({settings_path} does not exist)"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return f"ERROR: {settings_path} is not valid JSON. Remove Clawness hooks manually."
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return "OK: no hooks section — nothing to remove"

    removed = 0
    hooks = data["hooks"]
    for event in list(hooks.keys()):
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            continue
        new_groups = []
        for g in groups:
            inner = g.get("hooks", []) if isinstance(g, dict) else []
            kept = [h for h in inner if not _is_clawness_hook(h)]
            removed += len(inner) - len(kept)
            if kept:
                g["hooks"] = kept
                new_groups.append(g)
            elif not inner:
                new_groups.append(g)  # unrelated/empty group, leave as-is
        if new_groups:
            hooks[event] = new_groups
        else:
            del hooks[event]

    if dry_run:
        print(json.dumps(data, indent=2))
        return f"DRY RUN: would remove {removed} Clawness hook(s)."

    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"OK: removed {removed} Clawness hook(s) from {settings_path}"


def merge(settings_path: Path, hook_script: Path, dry_run: bool = False) -> str:
    """
    Merge Clawness hooks into settings.json. Returns a status message.
    Registers (each only if its script is present, each idempotent):
      - UserPromptSubmit: rule retrieval (claude_hook.py)
      - PostToolUse on Bash: output compression (compress_output.py)
      - PreToolUse + PostToolUse: plan gate (plan_gate.py)
      - PreToolUse + PostToolUse: access guard (access_guard.py)
      - SessionStart: git check, memory init, stack detect, trust ledger

    Kept in sync with .claude-plugin/plugin.json (the plugin-install path) — the
    two install methods must wire the same hooks. (ensure_deps.py is plugin-only:
    a manual install runs a real `pip install` so the deps are already present.)
    """
    # Read or create
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return f"ERROR: {settings_path} exists but is not valid JSON. Fix it manually."
        if not isinstance(data, dict):
            return f"ERROR: {settings_path} root is not a JSON object."
    else:
        data = {}

    if "hooks" not in data:
        data["hooks"] = {}

    results = []

    # --- UserPromptSubmit: rule retrieval ---
    if "UserPromptSubmit" not in data["hooks"]:
        data["hooks"]["UserPromptSubmit"] = []

    events = data["hooks"]["UserPromptSubmit"]
    if hook_already_present(events, hook_script):
        results.append("rules: already configured")
    else:
        entry = build_hook_entry(hook_script)
        events.append({"hooks": [entry]})
        results.append("rules: added")

    # --- PostToolUse: output compression ---
    compress_script = hook_script.resolve().parent / "compress_output.py"
    if compress_script.exists():
        if "PostToolUse" not in data["hooks"]:
            data["hooks"]["PostToolUse"] = []

        post_events = data["hooks"]["PostToolUse"]
        if hook_already_present(post_events, compress_script):
            results.append("compression: already configured")
        else:
            compress_entry = build_hook_entry(compress_script, timeout=10)
            post_events.append({
                "matcher": "Bash",
                "hooks": [compress_entry],
            })
            results.append("compression: added")

    # --- Plan gate (ON by default; native plan-mode approval clears it) ---
    plan_script = hook_script.resolve().parent / "plan_gate.py"
    if plan_script.exists():
        if "PreToolUse" not in data["hooks"]:
            data["hooks"]["PreToolUse"] = []
        if "PostToolUse" not in data["hooks"]:
            data["hooks"]["PostToolUse"] = []

        pre_events = data["hooks"]["PreToolUse"]
        post_events = data["hooks"]["PostToolUse"]

        # Check each side independently: a settings file with the PreToolUse entry
        # but a missing PostToolUse companion (hand-edited, or a partially-applied
        # earlier write) must be REPAIRED on re-run, not skipped because the Pre
        # side matched. Each append is idempotent (guarded by its own presence).
        added_any = False
        if not hook_already_present(pre_events, plan_script):
            pre_events.append({
                "matcher": "Write|Edit|MultiEdit|NotebookEdit",
                "hooks": [build_hook_entry(plan_script, timeout=10)],
            })
            added_any = True
        if not hook_already_present(post_events, plan_script):
            # PostToolUse: record approval on native plan approval (ExitPlanMode)
            # OR on the first edit the user approves, so the gate prompts at most
            # once per session instead of on every edit.
            post_events.append({
                "matcher": "ExitPlanMode|Write|Edit|MultiEdit|NotebookEdit",
                "hooks": [build_hook_entry(plan_script, timeout=10)],
            })
            added_any = True
        results.append(
            "plan-gate: added (on by default; `clawness plan off` to disable)"
            if added_any else "plan-gate: already configured")

    # --- Access guard (ON by default; PreToolUse classify + PostToolUse confirm) ---
    # Two events, one script: PreToolUse classifies each call (allow/ask/deny);
    # PostToolUse (same matcher) promotes an approved ask to "confirmed" so a
    # repeat doesn't re-prompt — see the two-phase ledger in clawness/guard.py.
    # Both must be wired or a declined ask would never get the chance to re-ask.
    guard_script = hook_script.resolve().parent / "access_guard.py"
    if guard_script.exists():
        if "PreToolUse" not in data["hooks"]:
            data["hooks"]["PreToolUse"] = []
        if "PostToolUse" not in data["hooks"]:
            data["hooks"]["PostToolUse"] = []

        pre_events = data["hooks"]["PreToolUse"]
        post_events = data["hooks"]["PostToolUse"]

        # Pre and Post are checked independently — a settings file with only the
        # PreToolUse classify hook (missing the PostToolUse confirm companion) would
        # otherwise never self-heal, and a declined ask would go silent for the rest
        # of the session (the exact hole the two-phase ledger closes).
        guard_matcher = "Bash|Write|Edit|MultiEdit|NotebookEdit|Read"
        added_any = False
        if not hook_already_present(pre_events, guard_script):
            pre_events.append({
                "matcher": guard_matcher,
                "hooks": [build_hook_entry(guard_script, timeout=10)],
            })
            added_any = True
        if not hook_already_present(post_events, guard_script):
            post_events.append({
                "matcher": guard_matcher,
                "hooks": [build_hook_entry(guard_script, timeout=10)],
            })
            added_any = True
        results.append(
            "access-guard: added (on by default; CLAW_NO_ACCESS_GUARD=1 to disable)"
            if added_any else "access-guard: already configured")

    # --- SessionStart: git presence check (asks before any git init) ---
    git_script = hook_script.resolve().parent / "git_check.py"
    if git_script.exists():
        if "SessionStart" not in data["hooks"]:
            data["hooks"]["SessionStart"] = []
        start_events = data["hooks"]["SessionStart"]
        if hook_already_present(start_events, git_script):
            results.append("git-check: already configured")
        else:
            start_events.append({
                "hooks": [build_hook_entry(git_script, timeout=10)],
            })
            results.append("git-check: added")

    # --- SessionStart: project memory bootstrap (creates .clawness/memory.md) ---
    memory_script = hook_script.resolve().parent / "memory_init.py"
    if memory_script.exists():
        if "SessionStart" not in data["hooks"]:
            data["hooks"]["SessionStart"] = []
        start_events = data["hooks"]["SessionStart"]
        if hook_already_present(start_events, memory_script):
            results.append("memory-init: already configured")
        else:
            start_events.append({
                "hooks": [build_hook_entry(memory_script, timeout=10)],
            })
            results.append("memory-init: added")

    # --- SessionStart: pick up a handoff left by the previous session ---
    handoff_script = hook_script.resolve().parent / "handoff_check.py"
    if handoff_script.exists():
        if "SessionStart" not in data["hooks"]:
            data["hooks"]["SessionStart"] = []
        start_events = data["hooks"]["SessionStart"]
        if hook_already_present(start_events, handoff_script):
            results.append("handoff-check: already configured")
        else:
            start_events.append({
                "hooks": [build_hook_entry(handoff_script, timeout=10)],
            })
            results.append("handoff-check: added")

    # --- SessionStart: project stack awareness (injects detected stack note) ---
    stack_script = hook_script.resolve().parent / "stack_detect.py"
    if stack_script.exists():
        if "SessionStart" not in data["hooks"]:
            data["hooks"]["SessionStart"] = []
        start_events = data["hooks"]["SessionStart"]
        if hook_already_present(start_events, stack_script):
            results.append("stack-detect: already configured")
        else:
            start_events.append({
                "hooks": [build_hook_entry(stack_script, timeout=10)],
            })
            results.append("stack-detect: added")

    # --- SessionStart: trust ledger (TOFU fingerprints of skills/agents/MCP) ---
    trust_script = hook_script.resolve().parent / "trust_ledger.py"
    if trust_script.exists():
        if "SessionStart" not in data["hooks"]:
            data["hooks"]["SessionStart"] = []
        start_events = data["hooks"]["SessionStart"]
        if hook_already_present(start_events, trust_script):
            results.append("trust-ledger: already configured")
        else:
            start_events.append({
                "hooks": [build_hook_entry(trust_script, timeout=10)],
            })
            results.append("trust-ledger: added (on by default; CLAW_NO_TRUST_LEDGER=1 to disable)")

    if dry_run:
        print(json.dumps(data, indent=2))
        return "DRY RUN: would write the above."

    # Write back
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
    return "OK: " + ", ".join(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up or remove Clawness hooks in Claude Code settings.")
    parser.add_argument("hook_script", type=Path, nargs="?", default=None, help="Path to claude_hook.py (install only)")
    parser.add_argument("--settings", type=Path, default=None, help="Path to settings.json")
    parser.add_argument("--dry-run", action="store_true", help="Print merged JSON without writing")
    parser.add_argument("--uninstall", action="store_true", help="Remove Clawness hooks from settings.json")
    args = parser.parse_args()

    settings = args.settings or default_settings_path()
    if args.uninstall:
        result = unmerge(settings, dry_run=args.dry_run)
    else:
        if args.hook_script is None:
            parser.error("hook_script is required unless --uninstall is given")
        result = merge(settings, args.hook_script, dry_run=args.dry_run)
    print(result)

    if result.startswith("ERROR"):
        sys.exit(1)


if __name__ == "__main__":
    main()
