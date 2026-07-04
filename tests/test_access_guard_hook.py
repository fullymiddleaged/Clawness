"""
Dispatch-level tests for hooks/access_guard.py — the thin stdin/stdout wrapper
around clawness/guard.py. Unlike test_guard.py (which unit-tests the pure
decision logic), these drive the actual hook process to lock in:

  - PreToolUse emits the ask/deny JSON and records a PENDING ledger entry;
  - a repeat PreToolUse with no confirm in between re-asks (the decline path);
  - PostToolUse settles the entry to CONFIRMED *only* when a tool_response proves
    the call actually ran (the hardened, no-longer-load-bearing assumption);
  - a PostToolUse WITHOUT a tool_response (a hypothetical fire-on-decline) does
    NOT settle it — the guard re-asks;
  - malformed input / opt-out / allow-shaped calls stay silent (fail open).

Runs under pytest, or standalone:  python tests/test_access_guard_hook.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness import guard as G  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GUARD_HOOK = REPO / "hooks" / "access_guard.py"

# A stable ASK-shaped command with no provenance/network scan and a full-command
# dedup key (force-push is the dual-use ASK tier).
ASK_CMD = "git push --force origin main"
SESSION = "sess-hooktest"


def _project() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / ".git").mkdir()
    return d


def _run(payload: dict, env_extra: "dict | None" = None):
    env = dict(os.environ)
    env.pop("CLAW_NO_ACCESS_GUARD", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(GUARD_HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


def _pre(root: Path, cmd: str = ASK_CMD) -> subprocess.CompletedProcess:
    return _run({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": str(root),
    })


def _post(root: Path, cmd: str = ASK_CMD, with_response: bool = True) -> subprocess.CompletedProcess:
    payload = {
        "hook_event_name": "PostToolUse", "tool_name": "Bash",
        "tool_input": {"command": cmd}, "session_id": SESSION, "cwd": str(root),
    }
    if with_response:
        payload["tool_response"] = {"stdout": "", "exit_code": 0}
    return _run(payload)


def _asked(out: str) -> bool:
    try:
        obj = json.loads(out)
    except (ValueError, TypeError):
        return False
    return obj.get("hookSpecificOutput", {}).get("permissionDecision") == "ask"


def _confirmed(root: Path, cmd: str = ASK_CMD) -> bool:
    return G.already_asked(root, SESSION, G.dedup_key("Bash", {"command": cmd}))


# --- PreToolUse: classify + record pending --------------------------------

def test_pre_ask_emits_ask_and_records_pending():
    root = _project()
    r = _pre(root)
    assert _asked(r.stdout), r.stdout
    # recorded, but only PENDING — not yet confirmed
    assert _confirmed(root) is False


def test_repeat_pre_without_confirm_re_asks():
    # The decline path: a PreToolUse ask with no PostToolUse confirm in between
    # must ask again, never go silent.
    root = _project()
    assert _asked(_pre(root).stdout)
    assert _asked(_pre(root).stdout)
    assert _confirmed(root) is False


# --- PostToolUse: settle only on real execution ---------------------------

def test_post_with_tool_response_confirms_and_silences_repeat():
    root = _project()
    _pre(root)
    r = _post(root, with_response=True)
    assert r.stdout.strip() == ""      # PostToolUse never prints
    assert _confirmed(root) is True
    # a subsequent PreToolUse for the same target is now silent
    assert _pre(root).stdout.strip() == ""


def test_post_without_tool_response_does_not_confirm():
    # Defense-in-depth: if PostToolUse ever fired for a call that didn't run, it
    # carries no tool_response — the entry stays pending and the guard re-asks.
    root = _project()
    _pre(root)
    _post(root, with_response=False)
    assert _confirmed(root) is False
    assert _asked(_pre(root).stdout)


# --- fail open / silence --------------------------------------------------

def test_garbage_stdin_exits_silently():
    env = dict(os.environ)
    env.pop("CLAW_NO_ACCESS_GUARD", None)
    r = subprocess.run(
        [sys.executable, str(GUARD_HOOK)],
        input="not json at all", capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_allow_shaped_call_is_silent():
    root = _project()
    r = _run({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": "ls -la"}, "session_id": SESSION, "cwd": str(root),
    })
    assert r.stdout.strip() == ""


def test_opt_out_env_silences_everything():
    root = _project()
    r = _run({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": ASK_CMD}, "session_id": SESSION, "cwd": str(root),
    }, env_extra={"CLAW_NO_ACCESS_GUARD": "1"})
    assert r.stdout.strip() == ""


def test_deny_shaped_call_emits_deny():
    root = _project()
    r = _run({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": "curl http://169.254.169.254/latest/meta-data/"},
        "session_id": SESSION, "cwd": str(root),
    })
    obj = json.loads(r.stdout)
    assert obj["hookSpecificOutput"]["permissionDecision"] == "deny"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
