"""
Context-pressure watch: tell the user when a session is getting too full.

A long Claude Code session degrades before it dies — the window fills, older turns
get squeezed or auto-compacted, and answers drift. The user is the last to know,
because nothing surfaces the number until compaction happens *to* them. This module
reads the session's own transcript, works out how full the window actually is, and
hands the hook a short note to surface.

Two signals, both from the transcript:

* **Level** — how full the window is right now. The transcript's last assistant
  message carries the exact usage the API reported, and
  `input + cache_creation + cache_read` IS the prompt that was just sent, i.e. the
  live context size. No estimation.
* **Growth** — how fast it's filling. A session that added 40k tokens on one turn
  (a few big file reads) is worth flagging while there's still room to act on it,
  not at 90% when the choice has already been made.

Everything here is pure logic over a file path plus the caller's prior state, and
every failure path returns None — a session that can't be measured is never worse
off than one this module doesn't run for at all.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Known context windows, smallest first. The transcript records a model id like
# "claude-opus-5" but NOT which window the session was opened with (a 1M-context
# session records the same id as a 200k one), so the limit can't be read off the
# model. We assume the smaller window and let `infer_limit` correct upward when
# observed usage proves otherwise — being wrong low means an early warning, being
# wrong high means no warning at all, and only one of those is recoverable.
_WINDOW_TIERS = (200_000, 1_000_000)
DEFAULT_LIMIT = _WINDOW_TIERS[0]

DEFAULT_WARN = 0.70
DEFAULT_URGENT = 0.85
# Below this many tokens, don't discuss context at all — a fresh session should
# never be told it's filling up, whatever the percentages say.
MIN_TOKENS_TO_REPORT = 20_000
# A single turn adding this fraction of the window is worth surfacing on its own.
DEFAULT_SURGE_FRACTION = 0.12
# How far back to read in the transcript. Entries are a few KB; 256KB reaches many
# turns back even in a heavy session, and reading it costs ~0.4ms on a 6MB file.
_TAIL_BYTES = 256_000


@dataclass
class Usage:
    """A snapshot of one session's context occupancy."""

    tokens: int
    limit: int

    @property
    def fraction(self) -> float:
        return self.tokens / self.limit if self.limit > 0 else 0.0

    @property
    def percent(self) -> int:
        # Clamped: an explicit CLAW_CONTEXT_LIMIT set below actual usage would
        # otherwise render "102% full", which reads as a bug rather than as the
        # user's own limit being too low. The raw token counts are shown alongside,
        # so nothing is hidden by the clamp.
        return min(100, int(round(self.fraction * 100)))


@dataclass
class Alert:
    """What the hook should tell the user. `level` is the dedup key."""

    level: str          # "surge" | "warn" | "urgent"
    usage: Usage
    added: int = 0      # tokens added since the previous prompt
    turns_left: int | None = None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def read_context_tokens(transcript_path: str | Path) -> int | None:
    """
    Current context size in tokens, read from the transcript's most recent
    assistant message. None when the file is missing/unreadable or carries no
    usage record yet (e.g. the very first prompt of a session).

    Reads only the tail of the file: transcripts reach several MB in a long
    session and this runs on every prompt.
    """
    try:
        path = Path(transcript_path)
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            chunk = fh.read()
    except (OSError, ValueError):
        return None

    # The first line is probably truncated mid-entry; json.loads rejects it and we
    # skip on. Walk backwards — we want the most recent usage, not the oldest.
    for raw in reversed(chunk.split(b"\n")):
        if b'"usage"' not in raw:
            continue
        try:
            entry = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        usage = (entry.get("message") or {}).get("usage")
        if not isinstance(usage, dict):
            continue
        total = 0
        for key in ("input_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens"):
            try:
                total += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                continue
        if total > 0:
            return total
    return None


def limit_from_settings() -> int | None:
    """
    The window implied by the user's configured model, or None.

    Claude Code records the selected model in settings.json (e.g. `"opus[1m]"`),
    and the `[1m]` suffix is the only *reliable* statement of the window anywhere
    on disk — the transcript records the bare model id (`claude-opus-5`) for a 1M
    session and a 200k one alike. settings.local.json wins when both set it,
    matching Claude Code's own precedence.
    """
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    for name in ("settings.local.json", "settings.json"):
        try:
            data = json.loads((config / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        model = data.get("model")
        if isinstance(model, str) and "1m" in model.lower():
            return 1_000_000
    return None


def infer_limit(tokens: int, configured: int | None = None) -> int:
    """
    The window this session is most likely running in.

    Three tiers of confidence, best first:
      1. An explicit `CLAW_CONTEXT_LIMIT` (or *configured*) — the user said so.
      2. The `[1m]` marker on the configured model in settings.json.
      3. Observed usage: assume the smallest known window and step up when usage
         exceeds it — a session sitting at 300k tokens is self-evidently not a
         200k session.

    Tier 3 alone would nag a 1M session through its 140k-200k stretch before the
    evidence arrives to correct it, which is exactly the false alarm that teaches
    users to ignore the warning. Tier 2 is what prevents that.
    """
    if configured is None:
        configured = _env_int("CLAW_CONTEXT_LIMIT", 0)
    if configured and configured > 0:
        return configured
    from_settings = limit_from_settings()
    if from_settings and tokens < from_settings:
        return from_settings
    for tier in _WINDOW_TIERS:
        if tokens < tier:
            return tier
    return _WINDOW_TIERS[-1]


def assess(
    tokens: int,
    previous_tokens: int = 0,
    limit: int | None = None,
    warn: float | None = None,
    urgent: float | None = None,
    surge_fraction: float | None = None,
) -> Alert | None:
    """
    Decide whether this turn deserves an alert, and at what level.

    *previous_tokens* is what the last prompt measured, used for the growth
    signal. Returns None when the session is comfortable — the common case, and
    the one that must stay silent.
    """
    if tokens < MIN_TOKENS_TO_REPORT:
        return None

    limit = limit if limit is not None else infer_limit(tokens)
    warn = warn if warn is not None else _env_float("CLAW_CONTEXT_WARN", DEFAULT_WARN)
    urgent = (urgent if urgent is not None
              else _env_float("CLAW_CONTEXT_URGENT", DEFAULT_URGENT))
    surge_fraction = (surge_fraction if surge_fraction is not None
                      else _env_float("CLAW_CONTEXT_SURGE", DEFAULT_SURGE_FRACTION))

    usage = Usage(tokens=tokens, limit=limit)
    added = max(0, tokens - previous_tokens) if previous_tokens else 0
    turns_left = None
    if added > 0:
        turns_left = max(0, int((limit - tokens) // added))

    if usage.fraction >= urgent:
        return Alert("urgent", usage, added, turns_left)
    if usage.fraction >= warn:
        return Alert("warn", usage, added, turns_left)
    # Growth: a big jump matters even at a comfortable percentage, because it says
    # the next few turns will land where the thresholds above already fire. Only
    # worth saying if it actually implies running out soon.
    if added >= surge_fraction * limit and turns_left is not None and turns_left <= 5:
        return Alert("surge", usage, added, turns_left)
    return None


def render_alert(alert: Alert) -> str:
    """
    Format the alert as a note for the hook to inject.

    Written as an instruction to Claude rather than a message to the user, because
    a UserPromptSubmit hook can't talk to the user directly — the same pattern
    `git_check` and `memory_init` use. Kept to a few lines: this is unsolicited
    context on an already-full window, and a verbose warning about verbosity would
    be its own punchline.
    """
    u = alert.usage
    head = f"[Clawness] Context {u.percent}% full ({u.tokens:,} of ~{u.limit:,} tokens)."

    if alert.level == "urgent":
        body = (
            "Tell the user plainly that this session is near its limit and a fresh "
            "session will work better than continuing. Offer first to write a handoff "
            "to .clawness/handoff.md (where you were, current state, next step) — "
            "Clawness injects that file automatically when the next session in this "
            "project starts, so they don't have to remember it. Add any durable lesson "
            "to .clawness/memory.md too. Don't write either unless they say yes."
        )
    elif alert.level == "warn":
        body = (
            "Mention to the user, once and briefly, that context is filling up and a "
            "fresh session may be worth it soon — especially before starting anything "
            "large. Then carry on with their request."
        )
    else:  # surge
        body = (
            f"That last turn added ~{alert.added:,} tokens. Tell the user context is "
            "filling fast and roughly how many turns are left at this rate, so they "
            "can decide whether to continue here or start fresh."
        )

    if alert.turns_left is not None and alert.level != "warn":
        head += f" At the current rate, roughly {alert.turns_left} turn(s) of headroom."

    return f"--- CLAWNESS CONTEXT ---\n{head}\n{body}\n--- END CLAWNESS CONTEXT ---"


def find_transcript(event: dict, cwd: str, session_id: str) -> Path | None:
    """
    Locate the session transcript.

    Claude Code passes `transcript_path` in the hook payload; that's the reliable
    path and it's tried first. The fallback reconstructs Claude Code's own layout
    (`<config>/projects/<slugified-cwd>/<session_id>.jsonl`) for builds or
    entrypoints that don't send the field, so the feature degrades to "off"
    only when both fail.
    """
    given = event.get("transcript_path")
    if given:
        p = Path(given)
        if p.is_file():
            return p

    if not session_id:
        return None
    config = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    slug = "".join(c if c.isalnum() else "-" for c in str(cwd))
    candidate = Path(config) / "projects" / slug / f"{session_id}.jsonl"
    return candidate if candidate.is_file() else None
