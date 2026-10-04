"""
Session handoff: leaving a note for the next session in this codebase.

The context watch tells a user when their session is too full to continue well and
offers to write a handoff. This module is the other half — where that handoff lives,
and how the *next* session finds it without the user having to remember it exists or
say anything at all.

It lives at `<project>/.clawness/handoff.md`, next to the lessons log, and the
SessionStart hook surfaces it automatically. The two files are deliberately
different things and shouldn't be merged:

  memory.md  — durable lessons about the codebase. Accumulates. Committed, shared.
  handoff.md — transient "here's where I was". One at a time, superseded, personal.

**The file's existence is the state.** A handoff sitting at that path is one nobody
has picked up yet — there's no "done" flag, no age cutoff, no heuristic guessing
whether it's still live. When it IS superseded (a new handoff gets written, or the
user says the work is finished) it moves to `.clawness/handoffs/done/`, which clears
the live slot and leaves a history. Nothing is ever deleted.

The note injects the handoff's CONTENT, not just a pointer to it. A pointer costs
the next session a tool call and, worse, relies on Claude choosing to follow it; the
whole point is that the user shouldn't have to shepherd this.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# Named for what it is, alongside memory.md in the same per-project directory.
HANDOFF_NAME = "handoff.md"
# Superseded handoffs land here. Kept rather than deleted: it costs nothing, and a
# handoff archived by mistake is otherwise unrecoverable work-in-progress notes.
DONE_DIR = ("handoffs", "done")
# Generous for what a handoff should be. WF-HANDOFF-001 asks for a short pointer —
# where we stopped, the next action, what's uncommitted — which lands well under this.
# A file that truncates here isn't being cut off, it's a status report that should
# have been a handoff; fix the writing, not this number.
DEFAULT_BUDGET = 2000

# Claude Code titles an unnamed session from the user's first message, so every pickup
# lands in their history as "carry on" — the one phrase every pickup shares, and so the
# one title that tells none of them apart. A SessionStart hook can now set
# `sessionTitle` (same effect as `/rename`), which the auto-start path uses; on a
# manual pickup the note can only suggest `/rename`, since the hook can't know yet
# whether the user will pick the handoff up at all. Matches the shape Claude Code's own generator produces — 2-4
# lowercase words, hyphen-separated — so a suggested name sits alongside a generated
# one without looking foreign.
SESSION_NAME_WORDS = 4
# Words every handoff heading carries, which therefore distinguish nothing. Dropped
# before the word budget is spent, not after.
_NAME_SKIP = frozenset({"handoff", "handoffs", "wip", "session", "notes"})
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.")

# Skeleton for whoever writes one (WF-HANDOFF-001 points here). Deliberately short:
# a handoff is a running start, not a status report, and a long one won't be read.
#
# `## Open questions` is what makes "carry on" safe to obey. The pickup instruction
# tells the next session to start work rather than interview the user, and that is
# only correct if genuine blocking decisions have somewhere to be written down.
# Expect it to say "none" — a handoff full of questions is one that stopped too early.
HANDOFF_TEMPLATE = """\
# Handoff — {date}

<a short paragraph: what we were doing, why, and exactly where it stopped>

**Next:** <the first thing to do — the command to run, or the file and change to make>

**Uncommitted:** <files left dirty or half-finished, or 'nothing'>

**Open questions:** <none — or the decisions genuinely blocked on the user, one line each>

**Autostart:** <yes if the user wants the next session to start on this without being asked, else no>
"""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def find_handoff(project_root: str | Path) -> Path | None:
    """The project's handoff file, if it exists."""
    try:
        path = Path(project_root) / ".clawness" / HANDOFF_NAME
        return path if path.is_file() else None
    except OSError:
        return None


def suggest_session_name(text: str) -> str:
    """
    A kebab-case session name from the handoff's first `# ` heading, or "".

    Returns "" rather than a bad guess in two cases, because a wrong suggestion is
    worse than none — the user has to read it, judge it and reject it:

      * no heading at all;
      * a heading with no letters left after cleaning. The template writes
        `# Handoff — {date}`, and once "handoff" is dropped that is a bare date;
        `/rename 2026-08-08` names nothing, so say nothing.
    """
    heading = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            heading = line[2:]
            break
    if not heading:
        return ""

    words = []
    for raw in heading.split():
        # Punctuation goes, but an internal dot stays: a version is the most
        # identifying thing a handoff heading carries, and "v1.9.0" beats "v190".
        word = "".join(c for c in raw.lower() if c in _NAME_CHARS).strip(".")
        if not word or word in _NAME_SKIP:
            continue
        words.append(word)
        if len(words) >= SESSION_NAME_WORDS:
            break

    if not any(c.isalpha() for c in "".join(words)):
        return ""
    return "-".join(words)


def describe_age(seconds: float) -> str:
    """Human-readable age. The next session's first question about a handoff is
    always 'how old is this?' — a note from an hour ago is a resume, one from last
    month is archaeology, and the two deserve different reactions."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return "just now" if minutes < 2 else f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return "an hour ago" if hours == 1 else f"{hours} hours ago"
    days = hours // 24
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    months = days // 30
    return "a month ago" if months == 1 else f"{months} months ago"


def archive_handoff(project_root: str | Path, now: float | None = None) -> Path | None:
    """
    Move the live handoff into `.clawness/handoffs/done/`, timestamped.

    Called when a handoff is superseded — a new one is being written, or the user
    says the work is done. Returns the archived path, or None if there was nothing
    to archive. Never deletes: the archive IS the delete, so an over-eager archive
    costs the user nothing.
    """
    path = find_handoff(project_root)
    if path is None:
        return None
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(now or time.time()))
    try:
        done = Path(project_root) / ".clawness" / Path(*DONE_DIR)
        done.mkdir(parents=True, exist_ok=True)
        target = done / f"{stamp}.md"
        # Two handoffs archived in the same second (tests, scripts) must not clobber
        # each other — the whole promise here is that nothing is lost.
        n = 2
        while target.exists():
            target = done / f"{stamp}-{n}.md"
            n += 1
        path.replace(target)
        return target
    except OSError:
        return None


def render_handoff_note(
    handoff_path: str | Path,
    budget: int | None = None,
    now: float | None = None,
    autostart: bool = False,
) -> str:
    """
    Build the SessionStart note for an existing handoff, or "" if unusable.

    Written as an instruction to Claude, since a hook can't address the user
    directly — the same pattern `git_check` and `memory_init` use. *autostart*
    swaps the conditional pickup for "begin on your first turn" (see autostart_due).
    """
    path = Path(handoff_path)
    try:
        text = path.read_text(encoding="utf-8").strip()
        mtime = path.stat().st_mtime
    except (OSError, UnicodeError):
        return ""
    if not text:
        return ""

    budget = budget if budget is not None else _env_int("CLAW_HANDOFF_BUDGET",
                                                        DEFAULT_BUDGET)

    truncated = False
    if len(text) > budget:
        # Keep the HEAD, unlike the lessons log: a handoff's summary and state are
        # written at the top, so the opening is the part worth having.
        text = text[:budget].rsplit("\n", 1)[0]
        truncated = True

    # Age is reported, never acted on. Whether the note is still live is answered by
    # the file being there at all; the age just tells the user whether they're
    # resuming this morning's work or something from months back.
    age = describe_age(max(0.0, (now if now is not None else time.time()) - mtime))

    # The instruction has to be conditional, not unconditional either way: SessionStart
    # fires BEFORE the user's first message, so this note cannot know whether they are
    # about to say "carry on" or "what's this?". Asking always was the old behaviour and
    # it wasted the handoff — the user writes one precisely so the next session doesn't
    # need an interview. Asking never would ambush someone who opened with a fresh task.
    instruction = (
        "It hasn't been picked up yet. If the user's first message asks to continue, "
        "carry on, resume or pick this up, then just do it: go straight to Next steps "
        "and start work, without an interview or a re-plan, asking only what the "
        "handoff lists under Open questions. Otherwise open the session by telling "
        "them, in two or three lines, where the last one left off and what comes next, "
        "then wait. When they say it's done (or you write a new handoff), move this "
        "file to .clawness/handoffs/done/ with a timestamped name instead of deleting it."
    )
    if autostart:
        # The user already answered "carry on" when they marked it, so the
        # conditional above becomes the default. The escape hatch survives: a first
        # message that is plainly a different task still wins.
        instruction = (
            "It is marked Autostart: the user asked for this session to continue it "
            "without being asked. Begin on Next at your first turn (an auto-start "
            "reminder may wake you before they type), going straight to the work "
            "and asking only what it lists under Open questions. Open with one line "
            "saying you're continuing from the handoff. If their first message is "
            "plainly a different task, do that instead and mention the handoff in one "
            "line. When the work is done (or you write a new handoff), move this file "
            "to .clawness/handoffs/done/ with a timestamped name instead of deleting it."
        )

    # Only on the pickup branch: if they opened with a fresh task instead, the handoff's
    # heading is the wrong name for the session they're actually in.
    #
    # Opt-IN, default OFF (CLAW_HANDOFF_SUGGEST_NAME): surfacing a `/rename` suggestion
    # on every pickup was more nagging than it was worth, so the clause is silent unless
    # a user turns it on. `suggest_session_name` stays fully functional for anyone who
    # does — this gates only whether the note mentions it.
    # Auto-start sets the title itself (sessionTitle), so there's nothing to suggest.
    name = (suggest_session_name(text)
            if os.environ.get("CLAW_HANDOFF_SUGGEST_NAME") and not autostart else "")
    if name:
        instruction += (
            " One aside, on the pickup branch only: an unnamed session takes its "
            "title from the user's first message, so this one will sit in their "
            "history as \"carry on\". Once you are underway, mention in a single "
            f"line that `/rename {name}` retitles it — they have to type it "
            "themselves. Say it once and drop it."
        )

    parts = [
        f"[Clawness] A handoff from the previous session in this project was written {age} "
        f"(.clawness/{HANDOFF_NAME}). {instruction}",
        "",
        "--- HANDOFF ---",
        text,
    ]
    if truncated:
        parts.append("(...truncated — full note in .clawness/handoff.md)")
    parts.append("--- END HANDOFF ---")
    return "\n".join(parts)


# --- Auto-start --------------------------------------------------------------
# A handoff exists so the next session needs no interview, and typing "carry on"
# is the last bit of interview left. A handoff marked `**Autostart:** yes` lets the
# next session begin by itself:
#
#   * interactive: a SessionStart hook registered with `asyncRewake` exits 2, which
#     wakes Claude with AUTOSTART_INSTRUCTION as a system reminder;
#   * headless (`claude -p`): handoff_check returns `initialUserMessage`, which
#     becomes the first turn with no prompt needed.
#
# Three guards keep it from ambushing someone who opened a session for something
# else, and they are the whole design:
#   1. Consent per handoff. The marker is written only when the user agreed to an
#      automatic pickup; a handoff without it behaves exactly as before.
#   2. Freshness (CLAW_HANDOFF_AUTOSTART_HOURS, default 12). Unlike the pickup NOTE,
#      which deliberately has no age cutoff, starting work unasked is an action, and
#      "continue what I left an hour ago" is not "continue what I left last month".
#   3. Once per handoff (interactive). The ledger stores the handoff's mtime, so a
#      second new session doesn't start the same work again, while a rewritten
#      handoff re-arms. Headless runs skip the ledger on purpose: a scripted loop
#      re-running `claude -p` is asking to continue every time.
# Only `startup` and `clear` sources qualify: resume/compact/fork are continuations
# of a conversation that already knows what it was doing.

DEFAULT_AUTOSTART_HOURS = 12
AUTOSTART_SOURCES = frozenset({"startup", "clear"})
_AUTOSTART_LEDGER = "handoff_autostart.json"
_AUTOSTART_RE = re.compile(r"^[\s*_>-]*autostart\s*[*_]*\s*:\s*[*_]*\s*(yes|true|on)\b",
                           re.IGNORECASE | re.MULTILINE)

AUTOSTART_INSTRUCTION = (
    "[Clawness] Auto-start: the previous session's handoff (.clawness/handoff.md) is "
    "marked Autostart, so the user asked for this session to pick it up without "
    "waiting for them. Start now: go straight to Next and begin work, asking only "
    "what it lists under Open questions. If the handoff isn't already in your "
    "context, read .clawness/handoff.md first. Open with one line saying you're "
    "continuing from the handoff, so the user can stop you if they meant something else."
)
AUTOSTART_USER_MESSAGE = "Carry on from the handoff in .clawness/handoff.md."


def wants_autostart(text: str) -> bool:
    """True when the handoff carries `Autostart: yes` (any bold/list decoration)."""
    return bool(_AUTOSTART_RE.search(text or ""))


def autostart_due(project_root: str | Path, source: str,
                  now: float | None = None) -> Path | None:
    """The handoff path when this session should start on it unasked, else None.
    Does not consult the once-per-handoff ledger; see claim_autostart."""
    try:
        if os.environ.get("CLAW_NO_HANDOFF_AUTOSTART"):
            return None
        if source not in AUTOSTART_SOURCES:
            return None
        path = find_handoff(project_root)
        if path is None:
            return None
        if not wants_autostart(path.read_text(encoding="utf-8")):
            return None
        hours = _env_int("CLAW_HANDOFF_AUTOSTART_HOURS", DEFAULT_AUTOSTART_HOURS)
        age = (now if now is not None else time.time()) - path.stat().st_mtime
        if age > hours * 3600:
            return None
        return path
    except (OSError, UnicodeError):
        return None


def autostart_claimed(project_root: str | Path, handoff_path: str | Path) -> bool:
    """True when this exact handoff (by mtime) has already auto-started."""
    try:
        mtime = Path(handoff_path).stat().st_mtime
        ledger = Path(project_root) / ".clawness" / _AUTOSTART_LEDGER
        return json.loads(ledger.read_text(encoding="utf-8")).get("mtime") == mtime
    except (OSError, ValueError, AttributeError):
        return False


def claim_autostart(project_root: str | Path, handoff_path: str | Path) -> bool:
    """Record that this handoff has auto-started; False if it already had.
    Keyed on the handoff's mtime, so rewriting the handoff re-arms it."""
    try:
        if autostart_claimed(project_root, handoff_path):
            return False
        mtime = Path(handoff_path).stat().st_mtime
        ledger = Path(project_root) / ".clawness" / _AUTOSTART_LEDGER
        from clawness.plan import atomic_write_text
        atomic_write_text(ledger, json.dumps({"mtime": mtime}) + "\n")
        return True
    except OSError:
        return False
