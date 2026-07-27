"""
Plan gate ("process keeper").

A gentle process keeper: before the first file edit of a session it PROMPTS
("proceed without a plan?") instead of hard-blocking — it rides Claude Code's
NATIVE plan mode and never invents a parallel command flow. The normal path
requires zero clawness-specific commands:

  - Plan-mode users: present a plan, the user approves it (ExitPlanMode), and the
    gate clears itself for the rest of the session — no prompt is ever shown.
  - Everyone else: the first edit surfaces a native approve dialog. Approving it
    (one click, a working Yes button — never a dead-end command) both lets the
    edit through AND clears the gate for the rest of the session, so the prompt
    appears at most once per session, not per edit.

  - Headless matches interactive, not a separate mode: `claude -p` sends the
    same `permission_mode` field on every hook call that an interactive session
    does, and the gate reads it the same way either way. `--permission-mode
    plan` plans and clears the gate on ExitPlanMode exactly like Shift+Tab does;
    `--permission-mode acceptEdits` (or `auto`/`dontAsk`/`bypassPermissions`)
    has already told Claude Code "edit without asking me", so the gate treats
    that as the same yes it would get from a clicked dialog and doesn't ask
    again — there is no one to ask in a headless run, and re-asking would just
    stall it. `default`/`plan` are live questions in both contexts and always
    prompt if unapproved. See PREAUTHORIZED_MODES.
  - ON by default, and the ONLY way to turn it off is global and deliberate:
    the CLAW_NO_PLAN_GATE environment variable, or `plan_gate.enabled: false`
    in the user-level <config>/clawness/config.json.
  - Approval is recorded automatically on native plan approval (ExitPlanMode) OR
    on the first edit the user approves.
  - Approval is per-session (each new session re-plans), keyed by Claude Code's
    session_id.
  - Fails OPEN: any unexpected error, or a missing/unrecognized permission_mode,
    defers to the normal permission flow (i.e. still asks) rather than silently
    letting an edit through.

Design note (1.5.0): there used to be TWO per-project kill switches, both
permanent and both silent — `plan off` wrote ``plan_gate.enabled: false`` into
<project>/.clawness/config.json, and `plan approve` wrote ``status: approved``
into <project>/.clawness/plan.json "until reset". Neither expired, neither
announced itself, and a plugin install does not ship the CLI that would undo
them. This repo's own gate sat off for a month that way before anyone noticed —
which is the exact failure a process keeper cannot have, because an absent
prompt is indistinguishable from a working one. Both are gone. The gate is now
session-scoped, full stop: an opt-out either dies with your shell (env var) or
is a global choice you made once for every project (user config). Nothing
project-local can silently disable it again.

Design note: this used to emit a hard ``deny``, which on the VS Code build has no
in-Claude override — a session with no recorded ExitPlanMode approval (e.g. the
user rejected it) got stranded, and the deny text pointed at ``clawness plan
approve``, a CLI the plugin install path does not put on PATH. An ``ask`` has a
working approve button, so the gate can never trap the user; that is why the
decision is ``ask``, not ``deny``.

State:
  - <project>/.clawness/sessions.json : { <session_id>: <approved_at>, ... }
    (auto-managed, pruned after 24h — the only per-project state there is)
  - <config>/clawness/config.json     : { plan_gate: { enabled } }
    (global opt-out, written by hand; absent means ON)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
PLAN_APPROVAL_TOOL = "ExitPlanMode"

_SESSION_TTL_SECONDS = 24 * 3600  # prune session approvals older than a day


def find_project_root(start: Optional[Path] = None) -> Path:
    """Walk up from *start* looking for an existing .clawness/ or .git/; fall back
    to *start* itself."""
    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".clawness").is_dir() or (candidate / ".git").is_dir():
            return candidate
    return start


def clawness_dir(root: Path) -> Path:
    return root / ".clawness"


def atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically: write a sibling temp file, then
    os.replace() it into place. A concurrent session (two Claude Codes in one
    project) can then never read a half-written ledger/cache — it sees either the
    old file or the new one, never a torn one. Best-effort: on any OSError the
    temp file is cleaned up and the write is dropped (callers already tolerate a
    missing/stale file and just re-ask)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _claude_config_dirs() -> list[Path]:
    """Claude Code config dir(s); honors CLAUDE_CONFIG_DIR (comma-separated),
    falling back to ~/.claude."""
    dirs: list[Path] = []
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        dirs += [Path(c.strip()).expanduser() for c in cfg.split(",") if c.strip()]
    dirs.append(Path.home() / ".claude")
    return dirs


def is_plan_file(target: "str | Path | None") -> bool:
    """True if *target* is a Claude Code plan-mode plan file (under
    ``<config>/plans/``).

    These writes happen DURING plan mode, *before* approval — they are how the
    plan that clears the gate gets written. Gating them is a catch-22 (you can't
    write the plan, so you can never approve one), so the gate must always
    exempt them."""
    if not target:
        return False
    try:
        p = Path(target).resolve()
    except Exception:
        return False
    for base in _claude_config_dirs():
        try:
            p.relative_to((base / "plans").resolve())
            return True
        except Exception:
            continue
    return False


# --- global opt-out (default ON) ------------------------------------------

def global_config_paths() -> list[Path]:
    """Where a user-level opt-out may live: <config>/clawness/config.json for
    each Claude Code config dir. Deliberately NOT per-project — see the module
    docstring."""
    return [d / "clawness" / "config.json" for d in _claude_config_dirs()]


def global_gate_disabled() -> bool:
    """True if the user turned the gate off for every project. Only an explicit
    ``plan_gate.enabled: false`` counts; a missing, unreadable or unrelated file
    leaves the gate ON, so a corrupt config can never silently disable it."""
    for path in global_config_paths():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        gate = cfg.get("plan_gate")
        if isinstance(gate, dict) and gate.get("enabled") is False:
            return True
    return False


def gate_enabled(root: Path) -> bool:
    """*root* is accepted for call-site symmetry but deliberately unused: no
    project-local file may disable the gate."""
    if os.environ.get("CLAW_NO_PLAN_GATE"):
        return False
    return not global_gate_disabled()


# --- per-session approval (native plan mode) ------------------------------

def _sessions_path(root: Path) -> Path:
    return clawness_dir(root) / "sessions.json"


def _load_sessions(root: Path) -> dict:
    try:
        return json.loads(_sessions_path(root).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_sessions(root: Path, sessions: dict) -> None:
    atomic_write_text(_sessions_path(root), json.dumps(sessions, indent=2) + "\n")


def record_session_approval(root: Path, session_id: str) -> None:
    """Mark the current session as plan-approved. Called when the user approves
    a plan via the native ExitPlanMode flow."""
    if not session_id:
        return
    sessions = _load_sessions(root)
    now = time.time()
    # prune stale entries to keep the file small
    sessions = {
        sid: ts for sid, ts in sessions.items()
        if isinstance(ts, (int, float)) and now - ts < _SESSION_TTL_SECONDS
    }
    sessions[session_id] = now
    _save_sessions(root, sessions)


def session_approved(root: Path, session_id: str) -> bool:
    if not session_id:
        return False
    ts = _load_sessions(root).get(session_id)
    return isinstance(ts, (int, float)) and (time.time() - ts) < _SESSION_TTL_SECONDS


# --- the gate decision ----------------------------------------------------

# NOTE: the opt-out named here must be one the reader can actually use. It used
# to name `clawness plan off` — a command the plugin install doesn't ship, and
# since 1.5.0 one that doesn't exist at all. Keep this to the env var, which
# needs nothing installed.
ASK_REASON = (
    "Clawness plan gate: no plan has been approved for this session yet. Approve "
    "this edit to proceed without a plan, or switch to plan mode (Shift+Tab) to plan "
    "first — either one clears the gate for the rest of the session, so you won't be "
    "asked again. (Opt out for this shell: set CLAW_NO_PLAN_GATE=1.)"
)

# Back-compat alias: earlier versions exported DENY_REASON.
DENY_REASON = ASK_REASON


# Permission modes in which the user has ALREADY answered "yes, edit without
# asking me" — at the harness level, for the whole session, in advance. Asking
# again is not a second safeguard, it is the same question a second time:
# interactively the answer is auto-supplied (the prompt never reaches a human),
# and headlessly there is no human to reach at all, so the ask can only stall a
# run the user explicitly set up to be unattended.
#
# This is what keeps headless and interactive intuitive: the gate does not care
# whether anyone is watching, only whether edits have been pre-authorised. A
# `claude -p` run with --permission-mode acceptEdits behaves exactly like an
# interactive session where the user pressed Shift+Tab — because it IS the same
# statement. Headless planning still works and still clears the gate the normal
# way: --permission-mode plan → ExitPlanMode → recorded, identical to the
# interactive path.
#
# "default" and "plan" are deliberately absent: in both, a permission prompt is
# still a live question. Values come from the documented `permission_mode` field
# on the hook payload; an unknown or missing value falls through to asking,
# which is the safe direction (a spurious prompt costs one click, a skipped one
# costs the whole point of the gate).
PREAUTHORIZED_MODES = frozenset({"acceptEdits", "auto", "dontAsk", "bypassPermissions"})


def gate_decision(
    root: Path,
    tool_name: str,
    session_id: str = "",
    target_path: "str | Path | None" = None,
    permission_mode: str = "",
) -> tuple[bool, str]:
    """Return (prompt, reason). prompt=True means the tool call should surface an
    approve dialog (permissionDecision="ask"), NOT a hard block — an unapproved
    session is nudged, never trapped. Fails open: any unexpected condition
    returns (False, "")."""
    try:
        if not gate_enabled(root):
            return (False, "")
        if tool_name not in WRITE_TOOLS:
            return (False, "")
        # Never gate writes to Claude Code's own plan file — those happen during
        # plan mode, before approval, and are how the gate gets cleared.
        if is_plan_file(target_path):
            return (False, "")
        if session_approved(root, session_id):
            return (False, "")
        if permission_mode in PREAUTHORIZED_MODES:
            return (False, "")
        return (True, ASK_REASON)
    except Exception:
        return (False, "")
