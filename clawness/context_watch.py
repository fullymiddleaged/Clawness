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
import re
from dataclasses import dataclass
from pathlib import Path

# Known context windows, smallest first. For models where the window is a choice
# (an opt-in `[1m]` variant), the transcript's model id can't say which one the
# session got, so we assume the smaller window and let `infer_limit` correct upward
# when observed usage proves otherwise — being wrong low means an early warning,
# being wrong high means no warning at all, and only one of those is recoverable.
_WINDOW_TIERS = (200_000, 1_000_000)
DEFAULT_LIMIT = _WINDOW_TIERS[0]

# Models whose window is NATIVELY 1M in Claude Code — no `[1m]` variant, no opt-in
# — per the model-config docs: "Fable 5.1, Fable 5, Sonnet 5 and later, and Opus
# 4.7 and later run with the 1M window on every plan". For these the id IS a
# reliable statement of the window, and assuming 200k told a 150k Opus 5.5 session
# it was 76% full when it was 15% — the false alarm that teaches users to ignore
# the real one. `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` makes Claude Code treat them as
# 200k again, so that env var turns this off. Matched with `search`, so Bedrock
# (`us.anthropic.claude-opus-4-7-v1:0`) and Vertex ids resolve too.
_NATIVE_1M_RE = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d+))?")


def native_window(model: str | None) -> int | None:
    """1M when *model* runs with a native 1M window, else None (unknown/opt-in)."""
    if not model or os.environ.get("CLAUDE_CODE_DISABLE_1M_CONTEXT", "") not in ("", "0"):
        return None
    m = _NATIVE_1M_RE.search(model.lower())
    if not m:
        return None
    family, major = m.group(1), int(m.group(2))
    # A long second component is a date suffix (claude-sonnet-4-20250514), not
    # a minor version.
    minor = int(m.group(3)) if m.group(3) and len(m.group(3)) <= 2 else 0
    if family in ("fable", "mythos"):
        return 1_000_000
    if family == "sonnet" and major >= 5:
        return 1_000_000
    if family == "opus" and (major, minor) >= (4, 7):
        return 1_000_000
    # "Haiku 5.5 ... run with the 1M window by default" (model-config docs).
    if family == "haiku" and (major, minor) >= (5, 5):
        return 1_000_000
    return None


# A Claude family name anywhere in the id — Claude Code resolves
# `anthropic/claude-opus-4-8` and `us.anthropic.claude-…-v1:0` to the model they
# name, so these are "recognized" for the declared-window rules below.
_CLAUDE_ID_RE = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)")


def declared_window(model: str | None) -> int | None:
    """
    The window the user declared to Claude Code itself, where Claude Code honours it.

    `CLAUDE_CODE_MAX_CONTEXT_TOKENS` is how a session on a gateway or non-Claude
    model (GLM via OpenRouter, a LiteLLM alias) tells Claude Code its real window,
    and Claude Code compacts at it — so reading the same variable keeps our
    percentages on the window Claude Code is actually working to, with nothing for
    the user to set twice. Per the model-config docs it applies only to an id
    Claude Code doesn't resolve to a Claude model, and not when that id carries
    `[1m]` (unless `CLAUDE_CODE_DISABLE_1M_CONTEXT` is set); a recognized Claude id
    honours it only under `DISABLE_COMPACT`. Mirroring those cases matters: a
    variable left over in the shell must not shrink a native-1M Opus session's
    window here when Claude Code itself is ignoring it.

    An unknown *model* (no usage record read yet) honours it — the user set it for
    this session's model, and the only id it wouldn't apply to is a Claude one.
    """
    raw = os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        # The env-var docs accept scientific notation and digit separators;
        # int() already takes `400_000`, float() covers `4e5`.
        try:
            value = int(float(raw))
        except (ValueError, OverflowError):
            return None
    if value <= 0:
        return None

    def _on(name: str) -> bool:
        return os.environ.get(name, "") not in ("", "0")

    if model:
        low = model.lower()
        if _CLAUDE_ID_RE.search(low):
            return value if _on("DISABLE_COMPACT") else None
        if "[1m]" in low and not _on("CLAUDE_CODE_DISABLE_1M_CONTEXT"):
            return None
    return value

# Thresholds as a fraction of the COMPACTION LINE — where this session will
# actually compact (`compaction_line`), not the raw window.
DEFAULT_WARN = 0.75
DEFAULT_URGENT = 0.90
# The pre-1.21 thresholds, as a fraction of the window. Still used when the
# window is all there is to go on: an explicit CLAW_CONTEXT_LIMIT, or
# compaction switched off (nothing compacts, so the window really is the edge).
WINDOW_WARN = 0.70
WINDOW_URGENT = 0.85
# Claude Code's idle compaction: past this size, the first message after a
# break compacts the session. Default and floor per the env-var docs for
# CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS ("Values below 100000 are raised").
DEFAULT_IDLE_FLOOR = 200_000
_IDLE_FLOOR_MIN = 100_000
# Where limit compaction fires below the window. The docs give ~967K for a
# native 1M window and "the 200K boundary" for 200k models; the user's own
# transcripts put the latter at ~167.6k. Both points fit window - 33k, so that
# is the default for any window; `compaction.learned_hard_point` overrides it
# per model wherever a real compaction has been seen.
_COMPACT_RESERVE = 33_000
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
    """A snapshot of one session's context occupancy.

    *line* is where the session will compact and *kind* what fires there:
    "limit" (auto-compact), "idle" (the next message after a break) or
    "window" (no compaction expected; the window itself is the edge).
    """

    tokens: int
    limit: int
    line: "int | None" = None
    kind: str = "window"

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
    switched: bool = False  # a model switch put the session past its new line


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
    """
    found = read_context(transcript_path)
    return found[0] if found else None


def read_context(transcript_path: str | Path) -> tuple[int, str] | None:
    """
    (tokens, model) from the transcript's most recent usage record, or None.
    The model id rides the same entry, so the window lookup costs no second read.

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
            model = (entry.get("message") or {}).get("model")
            return total, model if isinstance(model, str) else ""
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
    # Delegated so the settings read lives in one place (model_advisor owns
    # "what model is configured?"); the [1m] interpretation stays here.
    from clawness.model_advisor import read_settings_model
    model = read_settings_model()
    if isinstance(model, str) and "1m" in model.lower():
        return 1_000_000
    return None


def infer_limit(tokens: int, configured: int | None = None,
                model: str | None = None) -> int:
    """
    The window this session is most likely running in.

    Five tiers of confidence, best first:
      1. An explicit `CLAW_CONTEXT_LIMIT` (or *configured*) — the user said so.
      2. `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, where Claude Code itself honours it —
         the user said so to the harness (`declared_window`).
      3. The transcript's *model* id, for models whose window is natively 1M.
      4. The `[1m]` marker on the configured model in settings.json.
      5. Observed usage: assume the smallest known window and step up when usage
         exceeds it — a session sitting at 300k tokens is self-evidently not a
         200k session. This is also Claude Code's own default for an id it
         doesn't recognize (200K), so an undeclared GLM session agrees with it.

    Tier 4 alone would nag a 1M session through its 140k-200k stretch before the
    evidence arrives to correct it, which is exactly the false alarm that teaches
    users to ignore the warning. Tiers 2 and 3 are what prevent that.
    """
    if configured is None:
        configured = _env_int("CLAW_CONTEXT_LIMIT", 0)
    if configured and configured > 0:
        return configured
    declared = declared_window(model)
    if declared:
        return declared
    native = native_window(model)
    if native and tokens < native:
        return native
    from_settings = limit_from_settings()
    if from_settings and tokens < from_settings:
        return from_settings
    for tier in _WINDOW_TIERS:
        if tokens < tier:
            return tier
    return _WINDOW_TIERS[-1]


def _parse_tokens(raw) -> "int | None":
    """A token count as Claude Code accepts one: 400000, "400_000", "4e5",
    "200k", "1M". None for anything else, zero or negative."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = raw
    elif isinstance(raw, str):
        s = raw.strip().lower().replace("_", "").replace(",", "")
        scale = 1
        if s.endswith("k"):
            s, scale = s[:-1], 1_000
        elif s.endswith("m"):
            s, scale = s[:-1], 1_000_000
        try:
            value = float(s) * scale
        except ValueError:
            return None
    else:
        return None
    try:
        value = int(value)
    except (OverflowError, ValueError):
        return None
    return value if value > 0 else None


def _on(name: str) -> bool:
    return os.environ.get(name, "") not in ("", "0")


def compaction_disabled(layers: "list[dict] | None" = None) -> bool:
    """True when Claude Code won't auto-compact: then the window is the edge."""
    if _on("DISABLE_COMPACT") or _on("DISABLE_AUTO_COMPACT"):
        return True
    from clawness.model_advisor import read_setting
    return read_setting("autoCompactEnabled", layers) is False


def idle_floor() -> int:
    """Size past which the first message after a break compacts the session."""
    value = _parse_tokens(os.environ.get("CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS", ""))
    return max(_IDLE_FLOOR_MIN, value) if value else DEFAULT_IDLE_FLOOR


def auto_compact_window(layers: "list[dict] | None" = None) -> "int | None":
    """The user's auto-compact window, env var first (it "takes precedence over
    the command, the flag, and the `autoCompactWindow` setting"), else None.
    Values outside the documented 100K-1M range are ignored, not clamped."""
    value = _parse_tokens(os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW", ""))
    if value is None:
        from clawness.model_advisor import read_setting
        value = _parse_tokens(read_setting("autoCompactWindow", layers))
    if value is None or not (100_000 <= value <= 1_000_000):
        return None
    return value


def compaction_line(tokens: int, model: "str | None" = None,
                    window: "int | None" = None) -> "tuple[int, str]":
    """
    (line, kind): the size at which this session will compact, and why.

    The hard point is the lowest of: the window's default (window - 33k), the
    user's auto-compact window, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (which the docs
    say can only lower it), and what this model has been SEEN to compact at
    (`clawness.compaction`). Idle compaction then caps it: past the idle floor,
    a break compacts the session whatever the hard point says — which, on a 1M
    model, is what actually happens. Nothing here depends on a model list, so a
    new model with a bigger window needs no change.
    """
    from clawness.model_advisor import read_settings
    window = window or infer_limit(tokens, model=model)
    layers = read_settings()   # once: each file read costs ~0.3ms on Windows
    if compaction_disabled(layers):
        return window, "window"

    hard = max(window // 2, window - _COMPACT_RESERVE)
    acw = auto_compact_window(layers)
    if acw:
        hard = min(hard, acw, window)
    pct = _env_float("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", 0.0)
    if 1 <= pct <= 100:
        hard = min(hard, int(min(acw or window, window) * pct / 100))
    try:
        from clawness.compaction import learned_hard_point
        learned = learned_hard_point(model)
    except Exception:
        learned = None
    # Ignore a learned point under half the current hard point: it was recorded
    # under different settings (CLAUDE_CODE_DISABLE_1M_CONTEXT on a 1M model,
    # a small auto-compact window since removed), and once those are gone the
    # model never limit-compacts at that size again, so it would never age out.
    if learned and learned >= hard // 2:
        hard = min(hard, learned)

    floor = idle_floor()
    if floor < hard:
        return floor, "idle"
    return hard, "limit"


def assess(
    tokens: int,
    previous_tokens: int = 0,
    limit: int | None = None,
    warn: float | None = None,
    urgent: float | None = None,
    surge_fraction: float | None = None,
    model: str | None = None,
    previous_model: str | None = None,
) -> Alert | None:
    """
    Decide whether this turn deserves an alert, and at what level.

    Thresholds are fractions of the compaction line, so the alert lands before
    compaction rather than before a 1M window fills — which it never does. An
    explicit *limit* (or `CLAW_CONTEXT_LIMIT`) keeps the old percentage-of-window
    behaviour. *previous_tokens* is what the last prompt measured, used for the
    growth signal; across a model switch (*previous_model* differs) there is no
    meaningful growth, but a switch that lands the session past its new line is
    flagged. Returns None when the session is comfortable — the common case, and
    the one that must stay silent.
    """
    if tokens < MIN_TOKENS_TO_REPORT:
        return None

    explicit = limit if limit is not None else (_env_int("CLAW_CONTEXT_LIMIT", 0) or None)
    if explicit and explicit > 0:
        window, line, kind = explicit, explicit, "window"
    else:
        window = infer_limit(tokens, model=model)
        line, kind = compaction_line(tokens, model=model, window=window)
    by_window = kind == "window"

    warn = warn if warn is not None else _env_float(
        "CLAW_CONTEXT_WARN", WINDOW_WARN if by_window else DEFAULT_WARN)
    urgent = urgent if urgent is not None else _env_float(
        "CLAW_CONTEXT_URGENT", WINDOW_URGENT if by_window else DEFAULT_URGENT)
    surge_fraction = (surge_fraction if surge_fraction is not None
                      else _env_float("CLAW_CONTEXT_SURGE", DEFAULT_SURGE_FRACTION))

    usage = Usage(tokens=tokens, limit=window, line=line, kind=kind)
    changed_model = bool(model and previous_model and model != previous_model)
    # "Switched" means the switch LOWERED the line — the case that can put a
    # session past its compaction point without it having grown at all.
    switched = False
    if changed_model and not explicit:
        try:
            switched = compaction_line(tokens, model=previous_model)[0] > line
        except Exception:
            switched = False
    added = max(0, tokens - previous_tokens) if previous_tokens and not changed_model else 0
    turns_left = None
    if added > 0:
        turns_left = max(0, int((line - tokens) // added))

    reached = tokens / line if line > 0 else 0.0
    if reached >= urgent:
        return Alert("urgent", usage, added, turns_left, switched=switched)
    if reached >= warn:
        return Alert("warn", usage, added, turns_left)
    # Growth: a big jump matters even at a comfortable level, because it says
    # the next few turns will land where the thresholds above already fire. Only
    # worth saying if it actually implies running out soon.
    if added >= surge_fraction * line and turns_left is not None and turns_left <= 5:
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
    if u.kind == "window" or not u.line:
        head = f"[Clawness] Context {u.percent}% full ({u.tokens:,} of ~{u.limit:,} tokens)."
    else:
        left = u.line - u.tokens
        if left > 0:
            head = (f"[Clawness] Context is at {u.tokens:,} tokens; this session "
                    f"compacts at ~{u.line:,} (~{left:,} to go).")
        else:
            head = (f"[Clawness] Context is at {u.tokens:,} tokens, past the ~{u.line:,} "
                    "point where this session compacts.")
        if u.kind == "idle":
            head += (f" Past ~{u.line:,}, Claude Code compacts it on the first message "
                     "after a break of about an hour, so mention that a handoff before "
                     "stepping away avoids it.")
        if alert.switched:
            head += (" The model switch lowered the compaction point, so this session "
                     "is closer to it than it was a turn ago.")

    if alert.level == "urgent":
        body = (
            "Tell the user plainly that this session is close to being compacted, which "
            "summarises away detail, and that a fresh session will work better than "
            "continuing. Offer first to write a handoff "
            "to .clawness/handoff.md (where you were, current state, next step) that "
            "the next session picks up and starts on by itself: on a yes, write it with "
            "'**Autostart:** yes', then tell them to type /clear or open a new session, "
            "and work resumes without them typing anything else. Add any durable lesson "
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
