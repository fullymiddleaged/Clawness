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

import os
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
# one title that tells none of them apart. It ships a built-in `/rename [name]` for
# exactly this, but a slash command can only be TYPED: a hook cannot rename the session
# and neither can Claude. So the note carries a suggestion and the user spends one
# keystroke on it. Matches the shape Claude Code's own generator produces — 2-4
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
) -> str:
    """
    Build the SessionStart note for an existing handoff, or "" if unusable.

    Written as an instruction to Claude, since a hook can't address the user
    directly — the same pattern `git_check` and `memory_init` use.
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

    # Only on the pickup branch: if they opened with a fresh task instead, the handoff's
    # heading is the wrong name for the session they're actually in.
    name = suggest_session_name(text)
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
