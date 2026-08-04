#!/usr/bin/env python3
"""
Clawness — CLAUDE.md size check (SessionStart).

CLAUDE.md is loaded by the harness, in full, on every turn, before any hook runs.
That makes it the one context cost nothing in this plugin can cap: the rules block
is budgeted, the lessons log is ranked and budgeted, CLAUDE.md is neither. A file
that grew a paragraph at a time is invisible until someone measures it — this repo's
own is ~9,151 tokens a turn, about ten times its always-on rules block.

This hook measures it and, once, says so. It never edits the file: the note tells
Claude to announce the number to the user up front and offer a split, acting only on
agreement — the same consent shape as `git_check`'s `git init` and
`changelog_check`'s offer to create a changelog.

Design notes, in the order people try to "simplify" them:

  * The split is THREE-way, and the bulk does NOT go to memory.md. That file is
    sized for a lessons log — top-3 by relevance, ~1200 chars, 120 chars an entry,
    merge past 40 — so a 36k-char CLAUDE.md would be 300+ entries to surface three
    lines a turn, with the line cap shredding the rationale that was the payload.
    Long rationale attached to specific code belongs in `.clawness/rules/`: same
    ranking engine, no line cap, full rule format. Only one-line traps go to
    memory.md.

  * Anything shaped like "don't undo this without reading why" STAYS in CLAUDE.md.
    Retrieval is lossy, and that content is load-bearing exactly when the prompt
    gives no hint that it applies. A missed rule is a slightly worse answer; a missed
    "don't undo this" is the regression it existed to prevent.

  * Tokens are ESTIMATED at chars/4, never tokenized. PyYAML is this plugin's only
    dependency and a nudge is not a reason to change that. The note says "roughly"
    on purpose — a guess stated as a fact is a number people argue with instead of
    acting on.

  * `@path` imports are deliberately not followed. Resolving them needs the harness's
    import semantics verified first, and under-reporting a CLAUDE.md that was split
    into imports is a false negative, which costs nothing.

  * The ledger stores the SIZE, not a boolean. changelog_check asks a yes/no question
    once and is done; this one is a magnitude, and "asked at 6k, silent forever at
    30k" is precisely the failure mode this project already learned the hard way with
    the plan gate — an absent prompt is indistinguishable from a working one. So it
    re-arms once the file has grown by half again.

Gated to git work trees, never the home directory or filesystem root. Opt out with
CLAW_NO_CLAUDE_MD_CHECK=1, or drop a `.clawness/claude-md-check-off` marker in a
project that should never be asked. Fails open (silent) on every path.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Pins UTF-8 stdio at import and puts the repo on sys.path.
from _hookutil import git_root, read_payload, session_cwd  # noqa: E402

# The files the harness loads as project instructions. All of them are paid for on
# every turn, so all of them count toward the total.
CANDIDATES = ["CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"]

# Above this many estimated tokens, CLAUDE.md costs more per turn than Clawness's
# entire injection at full stretch (~851 mandatory + ~3,149 ranked), and pays it on
# every turn of every session forever. Below it, a project-instructions file is just
# doing its job — this hook says nothing, in line with the guard's rule about never
# nagging normal work.
DEFAULT_LIMIT_TOKENS = 6000

# Re-ask once the file is half again as big as it was when we last asked.
REARM_GROWTH = 1.5

# Rough tokens-per-character for English prose. Deliberately crude; see the docstring.
CHARS_PER_TOKEN = 4

NOTE = (
    "[Clawness] This project's CLAUDE.md is roughly {tokens:,} tokens ({chars:,} "
    "characters across {files}). It is re-read in full on every turn, before any hook "
    "runs — for comparison, Clawness's own rules block tops out around 4,000 tokens "
    "and is budgeted. Before doing anything else this session, tell the user this in "
    "two or three lines: the size, that it is a cost paid on every turn of every "
    "session, and that some of it would serve them better retrieved than re-read. "
    "Then offer the split, and act only if they agree. Durable orientation STAYS in "
    "CLAUDE.md — what the project is, key files, workflow, and the one-line form of "
    "every 'don't undo this', because retrieval cannot be relied on to fire when the "
    "prompt gives no hint. Long rationale attached to specific code moves to "
    "'.clawness/rules/' as rule files, which Clawness ranks and injects only when "
    "relevant. One-line traps that already bit go to '.clawness/memory.md'. Moving is "
    "destructive — the harness has already loaded CLAUDE.md by the time any hook runs, "
    "so a copy saves nothing and only deletion does — therefore confirm each moved "
    "section still surfaces before deleting its copy from CLAUDE.md. Never edit "
    "CLAUDE.md uninvited: plenty of projects want it exactly as it is. This is asked "
    "once per project, and again only if the file grows by half. Silence with "
    "CLAW_NO_CLAUDE_MD_CHECK=1."
)


def measure(root: Path) -> tuple[int, list[str]]:
    """Total characters of project-instruction files, and the ones that exist.

    Unreadable files are skipped rather than guessed at: a size we can't read is not
    a size worth reporting, and this hook's whole value is that its number is real.
    """
    total = 0
    found: list[str] = []
    for rel in CANDIDATES:
        path = root / rel
        try:
            if not path.is_file():
                continue
            # errors="replace" because only the length matters here, and a stray
            # byte in someone's CLAUDE.md is not a reason to go silent.
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(text)
        found.append(rel)
    return total, found


def limit_tokens() -> int:
    """Threshold in estimated tokens, from CLAW_CLAUDE_MD_LIMIT or the default."""
    raw = os.environ.get("CLAW_CLAUDE_MD_LIMIT", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LIMIT_TOKENS
    return value if value > 0 else DEFAULT_LIMIT_TOKENS


def should_ask(root: Path, tokens: int) -> bool:
    """True if this project hasn't been told, or has grown by half since it was.

    Records the size it asked at, so the next decision is a comparison rather than a
    boolean. An unwritable ledger still returns True: asking twice is a far smaller
    cost than never asking again.
    """
    path = root / ".clawness" / "claude_md.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("asked"):
            previous = data.get("tokens")
            if isinstance(previous, (int, float)) and previous > 0:
                if tokens < previous * REARM_GROWTH:
                    return False
            else:
                # Asked before, but we can't tell at what size — treat it as answered
                # rather than re-asking on every session from here on.
                return False
    except (OSError, ValueError):
        pass

    try:
        from clawness.plan import atomic_write_text
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path, json.dumps({"asked": time.time(), "tokens": tokens}, indent=2) + "\n"
        )
    except Exception:
        pass
    return True


def main() -> None:
    payload = read_payload()
    if payload is None:
        sys.exit(0)

    if os.environ.get("CLAW_NO_CLAUDE_MD_CHECK"):
        sys.exit(0)

    cwd_path = session_cwd(payload)
    if cwd_path is None:
        sys.exit(0)

    root = git_root(cwd_path)
    if root is None:
        sys.exit(0)

    if (root / ".clawness" / "claude-md-check-off").exists():
        sys.exit(0)

    chars, found = measure(root)
    if not found:
        sys.exit(0)

    tokens = chars // CHARS_PER_TOKEN
    if tokens < limit_tokens():
        sys.exit(0)

    # `should_ask` is called LAST, and records as it goes, so a session that would
    # have stayed quiet for any reason above doesn't spend the one shot.
    if should_ask(root, tokens):
        print(NOTE.format(tokens=tokens, chars=chars, files=", ".join(found)))
    sys.exit(0)


if __name__ == "__main__":
    main()
