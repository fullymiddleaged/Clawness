"""
Plan gate ("process keeper").

Inspired by infinri/Writ's process keeper, this blocks file edits until a plan
has been approved — but it rides Claude Code's NATIVE plan mode instead of
inventing a parallel command flow. The normal path requires zero writ-specific
commands: present a plan, the user approves it (ExitPlanMode), and the gate
clears itself for the rest of that session.

  - ON by default. Disable per-project with `clawness plan off`, or globally
    with the WRIT_NO_PLAN_GATE environment variable.
  - Approval is recorded automatically when the user approves a plan in plan
    mode. `clawness plan approve` is only a fallback for non-plan-mode or
    headless use.
  - Approval is per-session (each new session re-plans), keyed by Claude Code's
    session_id.
  - Fails OPEN: any unexpected error defers to the normal permission flow rather
    than blocking work.

State lives in <project>/.writ/:
  - config.json   : { plan_gate: { enabled } }
  - sessions.json : { <session_id>: <approved_at>, ... }   (auto-managed)
  - plan.json     : { status, approved_at }                (manual override)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
PLAN_APPROVAL_TOOL = "ExitPlanMode"

STATUS_NONE = "none"
STATUS_APPROVED = "approved"

_SESSION_TTL_SECONDS = 24 * 3600  # prune session approvals older than a day


def find_project_root(start: Optional[Path] = None) -> Path:
    """Walk up from *start* looking for an existing .writ/ or .git/; fall back
    to *start* itself."""
    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".writ").is_dir() or (candidate / ".git").is_dir():
            return candidate
    return start


def writ_dir(root: Path) -> Path:
    return root / ".writ"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --- config (default ON) --------------------------------------------------

def default_config() -> dict:
    return {"plan_gate": {"enabled": True}}


def load_config(root: Path) -> dict:
    base = default_config()
    path = writ_dir(root) / "config.json"
    try:
        cfg = json.loads(path.read_text())
        if isinstance(cfg.get("plan_gate"), dict):
            base["plan_gate"].update(cfg["plan_gate"])
    except Exception:
        pass
    return base


def save_config(root: Path, cfg: dict) -> None:
    d = writ_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")


def gate_enabled(root: Path) -> bool:
    if os.environ.get("WRIT_NO_PLAN_GATE"):
        return False
    return bool(load_config(root).get("plan_gate", {}).get("enabled", True))


# --- per-session approval (native plan mode) ------------------------------

def _sessions_path(root: Path) -> Path:
    return writ_dir(root) / "sessions.json"


def _load_sessions(root: Path) -> dict:
    try:
        return json.loads(_sessions_path(root).read_text())
    except Exception:
        return {}


def _save_sessions(root: Path, sessions: dict) -> None:
    d = writ_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    _sessions_path(root).write_text(json.dumps(sessions, indent=2) + "\n")


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


# --- manual override (fallback, no plan mode) -----------------------------

def default_state() -> dict:
    return {"status": STATUS_NONE, "approved_at": None}


def load_state(root: Path) -> dict:
    base = default_state()
    try:
        st = json.loads((writ_dir(root) / "plan.json").read_text())
        base.update({k: st[k] for k in base if k in st})
    except Exception:
        pass
    return base


def save_state(root: Path, state: dict) -> None:
    d = writ_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps(state, indent=2) + "\n")


def approve(root: Path) -> dict:
    """Manual, session-independent approval (fallback / headless use)."""
    st = {"status": STATUS_APPROVED, "approved_at": _now()}
    save_state(root, st)
    return st


def reset(root: Path) -> dict:
    save_state(root, default_state())
    return default_state()


def manually_approved(root: Path) -> bool:
    return load_state(root).get("status") == STATUS_APPROVED


# --- the gate decision ----------------------------------------------------

DENY_REASON = (
    "Writ plan gate: present an implementation plan and have the user approve it "
    "before editing files. Use plan mode — the gate clears automatically once the "
    "plan is approved, no commands needed. (Fallback: the user can run "
    "`clawness plan approve`; disable with `clawness plan off`.)"
)


def gate_decision(root: Path, tool_name: str, session_id: str = "") -> tuple[bool, str]:
    """Return (block, reason). block=True means deny the tool call.
    Fails open: any unexpected condition returns (False, "")."""
    try:
        if not gate_enabled(root):
            return (False, "")
        if tool_name not in WRITE_TOOLS:
            return (False, "")
        if session_approved(root, session_id) or manually_approved(root):
            return (False, "")
        return (True, DENY_REASON)
    except Exception:
        return (False, "")
