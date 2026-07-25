"""
Per-session state for the UserPromptSubmit hook.

Tracks how many prompts a Claude Code session has seen so hooks/claude_hook.py
can show the full mandatory-rule block on the first prompt (and periodically
after) while abbreviating it to an id list on the turns in between — the
identical mandatory block otherwise re-ships in full on every single prompt.
Also tracks project memory's mtime so a changed memory file is always shown in
full, regardless of cadence, and the session's last known context size (plus
which context alert has already been raised) for `clawness/context_watch.py`.

State lives in the OS temp dir, not the project: it is pure ephemeral cache
(never git-committed, never a security control), and a session's cwd can move
between prompts. No locking (mirrors guard.py/plan.py's existing session
ledgers) — a lost race just means one extra full render, never data loss.
Every function fails toward showing the full block (never toward silently
hiding content), and toward treating memory as changed rather than stale.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path


def _state_dir() -> Path:
    return Path(tempfile.gettempdir()) / "clawness-sessions"


def _state_path(session_id: str) -> Path:
    h = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return _state_dir() / f"{h}.json"


def _load(session_id: str) -> dict:
    try:
        data = json.loads(_state_path(session_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(session_id: str, state: dict) -> None:
    try:
        d = _state_dir()
        d.mkdir(parents=True, exist_ok=True)
        _state_path(session_id).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def bump_prompt_count(session_id: str) -> int:
    """Increment and return this session's prompt count (1 on the first call).

    A falsy session_id or any read/write error returns 1 — the "always show
    full" value — rather than silently defaulting to an abbreviated turn."""
    if not session_id:
        return 1
    state = _load(session_id)
    try:
        count = int(state.get("prompt_count", 0)) + 1
    except (TypeError, ValueError):
        count = 1
    state["prompt_count"] = count
    state["last_seen"] = time.time()
    _save(session_id, state)
    return count


def memory_changed(session_id: str, memory_path: "str | Path") -> bool:
    """True if *memory_path*'s mtime differs from what this session last saw
    (or nothing was recorded yet). Records the current mtime as a side effect.

    Drives `force_recent` in the memory block: a log written mid-session shows
    its newest entries on the next turn even if that prompt doesn't match them.
    Nothing recorded yet means this is the session's first prompt, so a fresh
    session also opens with the newest lessons visible.

    Fails toward True (show the newest entries) on a falsy session_id, a missing
    file, or any read/write error."""
    if not session_id:
        return True
    try:
        mtime = Path(memory_path).stat().st_mtime
    except OSError:
        return True
    state = _load(session_id)
    last = state.get("memory_mtime")
    changed = last is None or abs(float(last) - mtime) > 1e-6
    state["memory_mtime"] = mtime
    _save(session_id, state)
    return changed


def context_snapshot(session_id: str, tokens: int) -> int:
    """Record this prompt's context size and return the PREVIOUS one (0 if none).

    The delta between the two is the growth signal in `context_watch.assess`.
    Any error returns 0, which reads as "no growth data" and suppresses only the
    surge alert — the level thresholds still work off the absolute number."""
    if not session_id:
        return 0
    state = _load(session_id)
    try:
        previous = int(state.get("context_tokens", 0))
    except (TypeError, ValueError):
        previous = 0
    state["context_tokens"] = int(tokens)
    _save(session_id, state)
    return previous


def should_alert_context(session_id: str, level: str) -> bool:
    """True if *level* hasn't been raised for this session yet; records it.

    Context alerts fire on a condition that stays true once reached — without
    this the same warning would repeat every prompt for the rest of the session,
    which is how a useful nudge turns into noise the user learns to ignore. An
    escalation (warn -> urgent) is still allowed through; a repeat is not.

    Fails toward alerting: a missed warning is worse than a duplicated one."""
    if not session_id or not level:
        return True
    order = {"surge": 0, "warn": 1, "urgent": 2}
    state = _load(session_id)
    seen = state.get("context_alert")
    if seen == level or (
        seen in order and level in order and order[level] <= order[seen]
    ):
        return False
    state["context_alert"] = level
    _save(session_id, state)
    return True


def should_show_full(count: int, full_every: int) -> bool:
    """Whether this prompt count should get the full (not abbreviated) render.

    True on prompt 1 and every `full_every`-th prompt after (5 -> 1, 6, 11, 16,
    ...). full_every <= 1 always returns True — CLAW_FULL_EVERY=1 is how the
    old always-full behavior stays available."""
    if full_every <= 1:
        return True
    return (count - 1) % full_every == 0
