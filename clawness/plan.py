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

  - ON by default. Disable per-project with `clawness plan off`, or globally
    with the CLAW_NO_PLAN_GATE environment variable.
  - Approval is recorded automatically on native plan approval (ExitPlanMode) OR
    on the first edit the user approves. `clawness plan approve` remains a
    headless/CLI fallback; it is never the required path.
  - Approval is per-session (each new session re-plans), keyed by Claude Code's
    session_id.
  - Fails OPEN: any unexpected error defers to the normal permission flow rather
    than blocking work.

Design note: this used to emit a hard ``deny``, which on the VS Code build has no
in-Claude override — a session with no recorded ExitPlanMode approval (e.g. the
user rejected it) got stranded, and the deny text pointed at ``clawness plan
approve``, a CLI the plugin install path does not put on PATH. An ``ask`` has a
working approve button, so the gate can never trap the user; that is why the
decision is ``ask``, not ``deny``.

State lives in <project>/.clawness/:
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


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --- config (default ON) --------------------------------------------------

def default_config() -> dict:
    return {"plan_gate": {"enabled": True}}


def load_config(root: Path) -> dict:
    base = default_config()
    path = clawness_dir(root) / "config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(cfg.get("plan_gate"), dict):
            base["plan_gate"].update(cfg["plan_gate"])
    except Exception:
        pass
    return base


def save_config(root: Path, cfg: dict) -> None:
    d = clawness_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def gate_enabled(root: Path) -> bool:
    if os.environ.get("CLAW_NO_PLAN_GATE"):
        return False
    return bool(load_config(root).get("plan_gate", {}).get("enabled", True))


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


# --- manual override (fallback, no plan mode) -----------------------------

def default_state() -> dict:
    return {"status": STATUS_NONE, "approved_at": None}


def load_state(root: Path) -> dict:
    base = default_state()
    try:
        st = json.loads((clawness_dir(root) / "plan.json").read_text(encoding="utf-8"))
        base.update({k: st[k] for k in base if k in st})
    except Exception:
        pass
    return base


def save_state(root: Path, state: dict) -> None:
    atomic_write_text(clawness_dir(root) / "plan.json", json.dumps(state, indent=2) + "\n")


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

ASK_REASON = (
    "Clawness plan gate: no plan has been approved for this session yet. Approve "
    "this edit to proceed without a plan, or switch to plan mode (Shift+Tab) to plan "
    "first — either one clears the gate for the rest of the session, so you won't be "
    "asked again. (Opt out: `clawness plan off`, or CLAW_NO_PLAN_GATE=1.)"
)

# Back-compat alias: earlier versions exported DENY_REASON.
DENY_REASON = ASK_REASON


def gate_decision(
    root: Path,
    tool_name: str,
    session_id: str = "",
    target_path: "str | Path | None" = None,
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
        if session_approved(root, session_id) or manually_approved(root):
            return (False, "")
        return (True, ASK_REASON)
    except Exception:
        return (False, "")
